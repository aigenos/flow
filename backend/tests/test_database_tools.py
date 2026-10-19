"""Pin-tests for MongoDB/Redis tool factories — fake clients, no servers.

The contract under test is the security model: the OPERATOR's template defines
the query shape; caller args only fill typed value slots; unreferenced args
are ignored; missing referenced args fail closed.
"""
import pytest

from aigenos_flow.integrations.databases import (
    MissingToolArgError,
    MongoTools,
    RedisTools,
    bind_template,
    template_args,
)


# ------------------------------------------------------------------ fakes
class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sorted_by = None
        self.limited = None

    def sort(self, spec):
        self.sorted_by = spec
        return self

    def limit(self, n):
        self.limited = n
        return self

    def __iter__(self):
        rows = self.rows
        if self.limited is not None:
            rows = rows[: self.limited]
        return iter(rows)


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.last_filter = None
        self.last_projection = None

    def find(self, filter, projection=None):
        self.last_filter = filter
        self.last_projection = projection
        return FakeCursor(list(self.rows))

    def find_one(self, filter, projection=None):
        self.last_filter = filter
        for r in self.rows:
            if all(r.get(k) == v for k, v in filter.items() if not isinstance(v, dict)):
                return r
        return None

    def count_documents(self, filter):
        self.last_filter = filter
        return len(self.rows)

    def aggregate(self, pipeline):
        self.last_filter = pipeline
        return iter(list(self.rows))


class FakeDB(dict):
    def __getitem__(self, name):
        return self.setdefault(name, FakeCollection([]))


class FakeRedis:
    def __init__(self, store=None):
        self.store = store or {}

    def get(self, k):
        return self.store.get(k)

    def hgetall(self, k):
        return self.store.get(k) or {}

    def lrange(self, k, start, stop):
        rows = self.store.get(k) or []
        return rows if stop == -1 else rows[start : stop + 1]

    def set(self, k, v, ex=None):
        self.store[k] = v
        self.last_ttl = ex


# ------------------------------------------------------------- bind_template
def test_bind_whole_placeholder_keeps_type():
    out = bind_template({"amount": "{{amount}}"}, {"amount": 2500})
    assert out == {"amount": 2500} and isinstance(out["amount"], int)


def test_bind_nested_and_interpolated():
    t = {"full_name": {"$regex": "{{name}}", "$options": "i"}, "kind": "client-{{tier}}"}
    out = bind_template(t, {"name": "will", "tier": "gold"})
    assert out == {"full_name": {"$regex": "will", "$options": "i"}, "kind": "client-gold"}


def test_bind_missing_arg_fails_closed():
    with pytest.raises(MissingToolArgError, match="name"):
        bind_template({"full_name": "{{name}}"}, {})


def test_bind_ignores_unreferenced_args():
    # the model can't widen the query by inventing parameters
    out = bind_template({"id": "{{id}}"}, {"id": "C1", "$where": "evil", "extra": 1})
    assert out == {"id": "C1"}


def test_bind_regex_escape():
    out = bind_template(
        {"n": {"$regex": "{{q}}"}}, {"q": "a.b*"}, escape={"q"}
    )
    assert out["n"]["$regex"] == r"a\.b\*"


def test_template_args_walks_tree():
    assert template_args({"a": "{{x}}", "b": [{"c": "pre-{{y}}"}]}) == {"x", "y"}


# ------------------------------------------------------------------ mongo
def test_mongo_find_shapes_rows_for_pick(monkeypatch=None):
    db = FakeDB()
    db["clients"] = FakeCollection([
        {"id": "C1", "full_name": "William Smith"},
        {"id": "C2", "full_name": "Willa Williams"},
    ])
    mongo = MongoTools(db=db)
    tool = mongo.find(
        "search_clients", "clients",
        {"full_name": {"$regex": "{{name}}", "$options": "i"}},
        limit=10, description="Search clients by name.",
    )
    assert tool.__name__ == "search_clients"
    assert tool.description == "Search clients by name."
    out = tool({"name": "will", "sneaky": {"$gt": 0}})
    assert out["count"] == 2 and out["rows"][0]["id"] == "C1"
    # template structure preserved; only the value slot was bound
    assert db["clients"].last_filter == {"full_name": {"$regex": "will", "$options": "i"}}


def test_mongo_find_one_shapes_for_check_gate():
    db = FakeDB()
    db["accounts"] = FakeCollection([{"account_number": "ACC-1", "type": "auto"}])
    mongo = MongoTools(db=db)
    tool = mongo.find_one("validate_account", "accounts",
                          {"account_number": "{{account_number}}"})
    hit = tool({"account_number": "ACC-1"})
    miss = tool({"account_number": "ACC-9"})
    assert hit == {"found": True, "doc": {"account_number": "ACC-1", "type": "auto"}}
    assert miss == {"found": False, "doc": {}}


def test_mongo_requires_connection_info():
    with pytest.raises(ValueError, match="db= or uri="):
        MongoTools()


# ------------------------------------------------------------------ redis
def test_redis_hgetall_decodes_bytes_and_json():
    r = FakeRedis({
        "account:ACC-1:summary": {b"holder": b"William Smith", b"coverage": b'{"liability": "300k"}'},
    })
    tool = RedisTools(client=r).hgetall("account_summary", "account:{{account_number}}:summary")
    out = tool({"account_number": "ACC-1"})
    assert out["found"] is True
    assert out["doc"]["holder"] == "William Smith"
    assert out["doc"]["coverage"] == {"liability": "300k"}


def test_redis_lrange_shapes_rows():
    r = FakeRedis({"payments:ACC-1": [b'{"date": "2026-05-01", "amount": 128.5}']})
    tool = RedisTools(client=r).lrange("payment_history", "payments:{{account_number}}")
    out = tool({"account_number": "ACC-1"})
    assert out["rows"] == [{"date": "2026-05-01", "amount": 128.5}]


def test_redis_get_missing_key():
    tool = RedisTools(client=FakeRedis()).get("cfg", "cfg:{{k}}")
    assert tool({"k": "x"}) == {"found": False, "key": "cfg:x", "value": None}


def test_redis_set_writes_json_with_ttl():
    r = FakeRedis()
    tool = RedisTools(client=r).set_value("cache_summary", "sum:{{id}}", ttl_seconds=60)
    out = tool({"id": "A1", "value": {"x": 1}})
    assert out == {"ok": True, "key": "sum:A1"}
    assert r.store["sum:A1"] == '{"x": 1}' and r.last_ttl == 60


# ------------------------------------------- end-to-end: DB tools in a flow
def test_db_tools_drive_a_declarative_skill(tmp_path):
    from aigenos_flow import FlowAgent, initial_state
    from aigenos_flow.hosts.test_host import TestHost
    from aigenos_flow.interpret import SetField

    db = FakeDB()
    db["accounts"] = FakeCollection([{"account_number": "ACC-1", "type": "auto"}])
    mongo = MongoTools(db=db)

    d = tmp_path / "acct-check"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        "name: acct-check\n"
        "description: Validate an account against MongoDB.\n"
        "allowed-tools: validate_account\n"
        "fields:\n"
        "  account_number: {type: string, pattern: '^[A-Z0-9-]{1,20}$'}\n"
        "actions:\n"
        "  validate_account:\n"
        "    tool: validate_account\n"
        "    readonly: true\n"
        "    audit: false\n"
        "    args: {account_number: '{{account_number}}'}\n"
        "flow:\n"
        "  - ask: account_number\n"
        "  - do: validate_account\n"
        "  - check: 'validate_account.found'\n"
        "    else_say: 'No account {{account_number}} in the database.'\n"
        "    else_clear: [account_number]\n"
        "  - show: '{{validate_account.doc}}'\n"
        "    title: 'Account {{account_number}}'\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    host = TestHost()
    agent = FlowAgent.from_dir(
        tmp_path, host=host,
        tools=[mongo.find_one("validate_account", "accounts",
                              {"account_number": "{{account_number}}"})],
    )
    st = initial_state()
    st["flow_id"] = "acct-check"
    host.queue_commands("check ACC-9", SetField(field="account_number", value="ACC-9"))
    st["pending_user_message"] = "check ACC-9"
    st = agent.turn(st)
    assert any("No account ACC-9" in m for m in host.drain_pending_messages())

    host.queue_commands("ok ACC-1", SetField(field="account_number", value="ACC-1"))
    st["pending_user_message"] = "ok ACC-1"
    st = agent.turn(st)
    widgets = host.drain_pending_widgets()
    assert any(w["props"].get("title") == "Account ACC-1" for w in widgets)
