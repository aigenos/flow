"""Tools from an MCP server, for a flow to name.

    from aigenos_flow.mcp_tools import MCPTools

    travel = MCPTools(command=[sys.executable, "mcp_server.py"])          # a local server
    bank = MCPTools(url="https://mcp.example.com/mcp",                     # a remote one
                    headers={"Authorization": f"Bearer {token}"})
    agent = create_agent(llm, "skills/", tools={**travel.tools(), **bank.tools()})
    ...
    travel.close()

A skill then names the server's tools like any others (``do: hold_flight``,
``when: quote_trip.needs_approval``). Each tool arrives saying two things a
flow needs to know:

* **what it takes**, from the tool's input schema, so an answer's type comes
  from the tool and the tool is called with exactly its own inputs;
* **whether it only reads**, from the tool's ``readOnlyHint`` annotation. A tool
  without that hint is treated as one that changes something: no skill can run
  it without a ``confirm:`` above it, and it runs at most once. ``readonly=``
  names tools to treat as lookups when a server does not annotate them; that
  is your statement about the server, made once, in code.

The flow still decides which tool is called, when, and with which inputs. The
server only ever sees validated values.

Needs the ``mcp`` package (``pip install mcp``). A local server is started
from ``command`` and spoken to over stdio; a remote one is reached at ``url``
over streamable HTTP, or over SSE with ``transport="sse"`` for older servers.

Tools you already load with LangChain's MCP adapters work as they are too:
pass them in ``tools=``. Their input schema and ``readOnlyHint`` are read the
same way, and a step awaits them (see ``toolbox.invoke_langchain``).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
from typing import Any, AsyncIterator, Callable, Iterable, Sequence

from .toolbox import ToolInfo, attach_info
from .toolbox import params_from_json_schema as _params


def _unwrap(result: Any) -> Any:
    """An MCP tool result as the value the tool returned."""
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        text = " ".join(str(getattr(b, "text", "")) for b in getattr(result, "content", None) or [])
        raise RuntimeError(f"the MCP tool reported an error: {text.strip() or result!r}")
    for attr in ("structured_content", "structuredContent"):
        value = getattr(result, attr, None)
        if isinstance(value, dict):
            return value.get("result", value) if set(value) == {"result"} else value
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return {"text": text}
    return {}


class MCPTools:
    """One MCP server's tools, usable from synchronous code on any thread.

    The server is started as a subprocess and one session is kept open on a
    background event loop: a flow calls its tools synchronously, and an MCP
    client is asynchronous.
    """

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        transport: str = "streamable-http",
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        only: Iterable[str] | None = None,
        readonly: Iterable[str] = (),
        timeout: float = 60.0,
    ) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters  # noqa: F401
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised by the import guard test
            raise ModuleNotFoundError(
                "MCPTools needs the `mcp` package: pip install mcp"
            ) from exc
        if bool(command) == bool(url):
            raise ValueError(
                "MCPTools needs either the command that starts a local server, e.g. "
                "[sys.executable, 'server.py'], or url= for a remote one (not both)"
            )
        if transport not in ("streamable-http", "sse"):
            raise ValueError(f"transport is 'streamable-http' or 'sse', got {transport!r}")
        self.calls: list[tuple[str, dict]] = []     # every call made through this client, in order
        self._only = set(only) if only is not None else None
        self._readonly = set(readonly)
        self._timeout = timeout
        self._url = url
        self._headers = dict(headers or {})
        self._transport = transport
        self._command = [str(part) for part in command or ()]
        self._env = {**os.environ, **(env or {})}
        self._cwd = cwd
        self._listed: list[Any] = []
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._failed: BaseException | None = None
        self._session: Any = None
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._run, name="aigenos-flow-mcp", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise TimeoutError(f"the MCP server did not start within {timeout:.0f} s")
        if self._failed:
            raise RuntimeError(f"could not start the MCP server: {self._failed}") from self._failed

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    @contextlib.asynccontextmanager
    async def _streams(self) -> AsyncIterator[tuple[Any, Any]]:
        """The read and write streams to the server, however it is reached."""
        if not self._url:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self._command[0], args=self._command[1:], env=self._env, cwd=self._cwd,
            )
            async with stdio_client(params) as (read, write):
                yield read, write
            return
        if self._transport == "sse":
            from mcp.client.sse import sse_client

            async with sse_client(self._url, headers=self._headers or None) as streams:
                yield streams[0], streams[1]
            return
        try:  # mcp >= 2: headers travel on the HTTP client
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
        except ImportError:  # mcp 1.x
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(self._url, headers=self._headers or None) as streams:
                yield streams[0], streams[1]
            return
        async with create_mcp_http_client(headers=self._headers or None) as client:
            async with streamable_http_client(self._url, http_client=client) as streams:
                yield streams[0], streams[1]

    async def _serve(self) -> None:
        from mcp import ClientSession

        # Entered and exited in this one task: the transports require it.
        self._stop = asyncio.Event()
        try:
            async with self._streams() as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self._listed = list(getattr(listed, "tools", None) or [])
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the constructor
            self._failed = exc
            self._ready.set()

    # ------------------------------------------------------------------ calling
    def call(self, name: str, args: dict | None = None) -> Any:
        args = dict(args or {})
        self.calls.append((name, args))
        future = asyncio.run_coroutine_threadsafe(self._session.call_tool(name, args), self._loop)
        return _unwrap(future.result(timeout=self._timeout))

    def describe(self) -> dict[str, ToolInfo]:
        """What each of the server's tools takes, and whether it only reads."""
        out: dict[str, ToolInfo] = {}
        for tool in self._listed:
            name = str(getattr(tool, "name", ""))
            if not name or (self._only is not None and name not in self._only):
                continue
            schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
            hints = getattr(tool, "annotations", None)
            # The SDK spells it readOnlyHint (1.x) or read_only_hint (2.x).
            hinted = getattr(hints, "read_only_hint", None)
            if hinted is None:
                hinted = getattr(hints, "readOnlyHint", None)
            reads = hinted is True or name in self._readonly
            doc = str(getattr(tool, "description", "") or "").strip().splitlines()[0:1]
            out[name] = ToolInfo(
                name=name, readonly=reads, style="invoke", params=_params(schema),
                doc=doc[0].strip() if doc else "", source="mcp",
            )
        return out

    def tools(self) -> dict[str, Callable[[dict], Any]]:
        """``{name: fn(args) -> result}``: pass straight to ``tools=``."""
        out: dict[str, Callable[[dict], Any]] = {}
        for name, info in self.describe().items():
            def invoke(args: dict | None = None, _name: str = name) -> Any:
                return self.call(_name, args)

            invoke.__name__ = name
            invoke.__doc__ = info.doc
            out[name] = attach_info(invoke, info)
        return out

    def close(self) -> None:
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=10)

    def __enter__(self) -> "MCPTools":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
