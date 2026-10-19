"""A yes from someone else.

    - confirm: "Refund this order?"
      by: manager

The flow stops and waits. A request is filed here; the person named sees which
tool a yes will run and with which inputs, and decides. Until they do, nothing
below the step runs, and neither the user in the conversation nor the model can
answer in their place: the decision reaches the flow only through this store.

``ApprovalStore`` is in memory. A deployment that needs requests to survive a
restart, or to notify someone, gives its host an ``approvals`` attribute with
the same six methods (``request``, ``get``, ``decide``, ``withdraw``, ``all``,
``for_run``).

``who`` says who is asked. Nothing here checks who answers: that is for
whatever sits in front of ``decide`` (the server puts it behind the same access
rule as authoring).
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Collection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ApprovalRequest:
    id: str
    who: str                    # who is asked: "manager", "finance"
    question: str
    skill: str
    run_id: str
    field: str
    #: What a yes will run: ``[{"title", "note", "inputs": [{"label", "shown"}]}]``.
    runs: list[dict[str, Any]] = field(default_factory=list)
    #: What the user has answered so far, for context (never a secret).
    answers: list[dict[str, str]] = field(default_factory=list)
    status: str = "pending"     # pending | approved | declined | withdrawn
    decided_by: str = ""
    note: str = ""
    created: str = field(default_factory=_now)
    decided: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApprovalStore:
    """Requests waiting for someone's yes, and what they answered."""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()

    def request(self, *, run_id: str, field: str, who: str, question: str, skill: str,
                runs: list[dict[str, Any]], answers: list[dict[str, str]]) -> ApprovalRequest:
        """File a request, or return the one already filed for exactly this.

        The id is a digest of the run, the step and everything shown, so the
        same request is found again on every turn, and a request for something
        that has since changed is a new one: the old one is withdrawn, because
        a yes to it would be a yes to something that will not run.
        """
        digest = hashlib.sha256(json.dumps(
            [run_id, field, who, question, runs, answers], sort_keys=True, default=str,
        ).encode("utf-8")).hexdigest()
        request_id = "APR-" + digest[:6].upper()
        with self._lock:
            for other in self._requests.values():
                if other.run_id == run_id and other.field == field and other.id != request_id \
                        and other.status == "pending":
                    other.status, other.decided = "withdrawn", _now()
            found = self._requests.get(request_id)
            if found is None:
                found = ApprovalRequest(
                    id=request_id, who=who, question=question, skill=skill, run_id=run_id,
                    field=field, runs=runs, answers=answers,
                )
                self._requests[request_id] = found
            elif found.status == "withdrawn":
                # Asked for again with nothing about it changed (an answer was
                # changed and changed back): it stands again.
                found.status, found.decided, found.created = "pending", "", _now()
            return found

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._requests.get(request_id)

    def decide(self, request_id: str, approved: bool, *, by: str = "", note: str = "") -> ApprovalRequest:
        """Record the answer. A request already answered, or withdrawn, stays as it is."""
        with self._lock:
            found = self._requests.get(request_id)
            if found is None:
                raise KeyError(f"no approval request {request_id!r}")
            if found.status != "pending":
                raise ValueError(f"{request_id} is already {found.status}")
            found.status = "approved" if approved else "declined"
            found.decided_by, found.note, found.decided = by.strip(), note.strip(), _now()
            return found

    def withdraw(self, run_id: str, keep: Collection[str] = ()) -> None:
        """This flow's open requests no longer stand, except the ones in ``keep``.

        Called with nothing to keep when the flow is cancelled or started over,
        and after every turn with the request the flow is waiting on now: one
        it has moved away from (an answer above it changed) leaves the inbox.
        """
        with self._lock:
            for found in self._requests.values():
                if found.run_id == run_id and found.status == "pending" and found.id not in keep:
                    found.status, found.decided = "withdrawn", _now()

    def all(self, status: str | None = None) -> list[ApprovalRequest]:
        found = sorted(self._requests.values(), key=lambda r: r.created, reverse=True)
        return [r for r in found if status is None or r.status == status]

    def for_run(self, run_id: str) -> list[ApprovalRequest]:
        return [r for r in self.all() if r.run_id == run_id]


#: Used when a host does not bring its own.
default_store = ApprovalStore()


def store_for(host: Any) -> Any:
    """The store a flow files its requests in: the host's, or the shared default."""
    return getattr(host, "approvals", None) or default_store
