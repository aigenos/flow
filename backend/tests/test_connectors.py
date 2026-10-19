from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow.connectors.manager import ConnectorManager
from aigenos_flow.connectors.models import ManagedOperation
from aigenos_flow.connectors.mongodb import substitute_template, validate_mongo_operation
from aigenos_flow.connectors.postgresql import validate_sql
from aigenos_flow.connectors.secrets import SecretResolver
from aigenos_flow.connectors.store import ConnectorStore
from aigenos_flow.declarative import ToolRegistry
from aigenos_flow.skills import load_skill


def pg_operation(**changes):
    data = {
        "id": "op1", "connection_id": "conn1", "provider": "postgresql",
        "name": "find_customer", "display_name": "Find customer",
        "tool_name": "db__billing__find_customer", "kind": "select", "mode": "read",
        "definition": {"sql": "SELECT id FROM customers WHERE id = %(id)s"},
        "parameters": {"id": {"type": "integer"}},
    }
    data.update(changes)
    return ManagedOperation.model_validate(data)


def mongo_operation(**changes):
    data = {
        "id": "op2", "connection_id": "conn2", "provider": "mongodb",
        "name": "find_customer", "display_name": "Find customer",
        "tool_name": "db__crm__find_customer", "kind": "find_one", "mode": "read",
        "definition": {"collection": "customers", "filter": {"customer_id": "{{customer_id}}"}},
        "parameters": {"customer_id": {"type": "string"}},
    }
    data.update(changes)
    return ManagedOperation.model_validate(data)


def test_inline_secret_roundtrip_and_wrong_key_fails():
    resolver = SecretResolver("unit-test-key")
    envelope = resolver.encrypt("postgresql://user:password@db/app")
    assert "password" not in envelope
    assert resolver.decrypt(envelope) == "postgresql://user:password@db/app"
    with pytest.raises(Exception):
        SecretResolver("different-key").decrypt(envelope)


def test_environment_secret_resolution(monkeypatch):
    monkeypatch.setenv("TEST_CONNECTOR_URI", "mongodb://user:pass@db/app")
    resolver = SecretResolver("unused")
    assert resolver.resolve(encrypted=None, env_reference="TEST_CONNECTOR_URI").startswith("mongodb://")
    monkeypatch.delenv("TEST_CONNECTOR_URI")
    with pytest.raises(RuntimeError, match="not set"):
        resolver.resolve(encrypted=None, env_reference="TEST_CONNECTOR_URI")


def test_store_never_returns_credentials_and_keeps_stable_tool(tmp_path: Path):
    store = ConnectorStore(tmp_path / "connectors.sqlite")
    manager = ConnectorManager(store, SecretResolver("master"))
    connection = manager.create_connection({
        "provider": "postgresql", "name": "Billing", "access_level": "read_write",
        "credential": "postgresql://user:password@db/app?sslmode=verify-full",
    })
    assert "credential_encrypted" not in connection
    assert "credential_env" not in connection
    operation = manager.prepare_operation(connection["id"], pg_operation().model_dump(exclude={"id", "connection_id", "provider", "tool_name"}))
    saved = manager.commit_operation(operation, create=True)
    renamed = manager.prepare_operation(
        connection["id"],
        {**saved.model_dump(), "name": "lookup_customer", "display_name": "Customer lookup"},
        current=saved,
    )
    updated = manager.commit_operation(renamed, create=False)
    assert updated.tool_name == saved.tool_name
    assert updated.revision == 2


@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "SELECT 1; SELECT 2",
    "UPDATE customers SET active=false",
    "COPY customers TO STDOUT",
])
def test_postgres_rejects_unsafe_sql(sql: str):
    mode = "read" if sql.startswith("SELECT") else "write"
    kind = "select" if mode == "read" else "update"
    with pytest.raises(ValueError):
        validate_sql(pg_operation(kind=kind, mode=mode, definition={"sql": sql}, parameters={}))


def test_postgres_native_parameter_match_is_exact():
    assert validate_sql(pg_operation())["parameters"] == ["id"]
    with pytest.raises(ValueError, match="parameter mismatch"):
        validate_sql(pg_operation(parameters={"other": {"type": "integer"}}))


def test_mongo_typed_substitution_and_interpolation_rejection():
    template = {"customer_id": "{{customer_id}}", "active": True}
    assert substitute_template(template, {"customer_id": 42})["customer_id"] == 42
    with pytest.raises(ValueError, match="interpolation"):
        substitute_template({"customer_id": "prefix-{{customer_id}}"}, {"customer_id": 42})


@pytest.mark.parametrize("definition", [
    {"collection": "x", "filter": {}},
    {"collection": "x", "filter": {"$where": "this.x"}},
])
def test_mongo_rejects_empty_mutation_filters_and_code_operators(definition):
    with pytest.raises(ValueError):
        validate_mongo_operation(mongo_operation(
            kind="delete_many", mode="write", definition=definition, parameters={}
        ))


def test_managed_write_requires_confirmation_and_audit(tmp_path: Path):
    folder = tmp_path / "change-customer"
    folder.mkdir()
    folder.joinpath("SKILL.md").write_text("""---
name: change-customer
description: Change one customer after explicit approval.
allowed-tools: db__billing__change_customer
fields:
  customer_id: {type: integer}
actions:
  change_customer:
    tool: db__billing__change_customer
    audit: false
    args: {id: '{{customer_id}}'}
flow:
  - ask: customer_id
  - do: change_customer
---
Body.
""", encoding="utf-8")
    descriptor = pg_operation(
        id="write1", name="change_customer", display_name="Change customer",
        tool_name="db__billing__change_customer", kind="update", mode="write",
        definition={"sql": "UPDATE customers SET active=false WHERE id=%(id)s"},
    )
    from aigenos_flow.connectors.models import ToolDescriptor
    registry = ToolRegistry()
    registry.register(
        descriptor.tool_name,
        lambda args: {"affected_count": 1},
        descriptor=ToolDescriptor(
            name=descriptor.tool_name, operation_id=descriptor.id,
            side_effecting=True, parameters=descriptor.parameters,
        ),
    )
    with pytest.raises(ValueError, match="must declare `requires:`|cannot disable auditing"):
        load_skill(folder, tool_registry=registry)


def test_management_api_requires_token_masks_secret_and_never_executes_write(
    tmp_path: Path, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from aigenos_flow.connectors.api import make_connector_router

    monkeypatch.setenv("FG_CONNECTOR_ADMIN_TOKEN", "admin-test-token")
    manager = ConnectorManager(
        ConnectorStore(tmp_path / "api.sqlite"), SecretResolver("master")
    )
    app = FastAPI()
    app.include_router(make_connector_router(
        manager,
        validate_candidate=lambda registry: object(),
        apply_candidate=lambda skills, registry: None,
        skills_dir=str(tmp_path),
    ))
    client = TestClient(app)
    assert client.get("/connectors/providers").status_code == 401
    headers = {"x-connector-admin-token": "admin-test-token"}
    response = client.post("/connectors", headers=headers, json={
        "provider": "postgresql", "name": "Billing",
        "credential": "postgresql://user:password@db/app", "access_level": "read_write",
    })
    assert response.status_code == 201
    connection = response.json()
    assert "password" not in response.text
    body = {
        "name": "deactivate_customer", "display_name": "Deactivate customer",
        "kind": "update", "mode": "write",
        "definition": {"sql": "UPDATE customers SET active=false WHERE id=%(id)s"},
        "parameters": {"id": {"type": "integer"}}, "timeout_seconds": 10,
        "row_limit": 100, "result_limit_bytes": 1_048_576, "enabled": True,
    }
    created = client.post(
        f"/connectors/{connection['id']}/operations", headers=headers, json=body
    )
    assert created.status_code == 201, created.text
    tested = client.post(
        f"/connectors/operations/{created.json()['id']}/test",
        headers=headers,
        json={"parameters": {"id": 7}},
    )
    assert tested.status_code == 200
    assert tested.json()["executed"] is False


def test_runtime_audit_is_persistent_redacted_and_has_no_result_body(tmp_path: Path):
    from aigenos_flow.connectors.models import ConnectorResult, InvocationContext

    class FakeAdapter:
        provider = "postgresql"
        def validate_operation(self, operation): return {"valid": True}
        def execute(self, uri, operation, parameters):
            assert "password" in uri
            return ConnectorResult(
                rows=[{"private_result": "must-not-be-audited"}], row_count=1,
                execution_id="execution-1",
            )
        def invalidate(self, connection_id): return None

    store = ConnectorStore(tmp_path / "audit.sqlite")
    manager = ConnectorManager(store, SecretResolver("master"))
    manager.adapters["postgresql"] = FakeAdapter()
    connection = manager.create_connection({
        "provider": "postgresql", "name": "Billing", "access_level": "read",
        "credential": "postgresql://user:password@db/app?sslmode=verify-full",
    })
    operation = manager.prepare_operation(connection["id"], {
        "name": "find", "display_name": "Find", "kind": "select", "mode": "read",
        "definition": {"sql": "not inspected by fake"},
        "parameters": {"token": {"type": "string", "sensitive": True}},
    })
    manager.commit_operation(operation, create=True)
    result = manager.invoke(
        operation.id, {"token": "secret-input"},
        context=InvocationContext(skill_name="lookup", action_name="find", flow_run_id="run-1"),
    )
    assert result["rows"][0]["private_result"] == "must-not-be-audited"
    audit = store.list_audits()[0]
    assert audit["parameters"] == {"token": "***REDACTED***"}
    assert audit["skill_name"] == "lookup"
    assert "private_result" not in str(audit)


# ------------------------------------------------------------------ server-2
# The audit trail used to redact ONLY `sensitive: true` parameters plus names
# matching pass|secret|token|credential|api.?key|otp|ssn, so card_number, cvv,
# iban, email, dob, account_number, home_address and salary were all written to
# audit_events.parameters_json in cleartext and returned by
# GET /flow/connectors/audits. Redaction is now default-deny.

def test_audit_redacts_unconstrained_values_by_default():
    from aigenos_flow.connectors.manager import REDACTED, redact_parameters
    from aigenos_flow.connectors.models import ParameterSpec

    specs = {
        "card_number": ParameterSpec(type="string"),
        "cvv": ParameterSpec(type="string"),
        "iban": ParameterSpec(type="string"),
        "email": ParameterSpec(type="string"),
        "dob": ParameterSpec(type="string"),
        "account_number": ParameterSpec(type="string"),
        "home_address": ParameterSpec(type="string"),
        "notes": ParameterSpec(type="object"),
        "tags": ParameterSpec(type="array"),
    }
    values = {
        "card_number": "4111111111111111",
        "cvv": "737",
        "iban": "GB33BUKB20201555555555",
        "email": "victim@example.com",
        "dob": "1971-03-02",
        "account_number": "00123456",
        "home_address": "1 Alder Way",
        "notes": {"free": "text"},
        "tags": ["a"],
    }
    redacted = redact_parameters(specs, values)
    assert set(redacted) == set(values)
    assert all(v == REDACTED for v in redacted.values()), redacted
    for secret in values.values():
        assert str(secret) not in str(redacted)


def test_audit_keeps_the_values_that_cannot_carry_a_secret():
    from aigenos_flow.connectors.manager import redact_parameters
    from aigenos_flow.connectors.models import ParameterSpec

    specs = {
        "amount": ParameterSpec(type="number"),
        "limit": ParameterSpec(type="integer"),
        "confirmed": ParameterSpec(type="boolean"),
        "tier": ParameterSpec(type="string", enum=["gold", "silver"]),
        "region": ParameterSpec(type="string", log_value=True),
    }
    values = {
        "amount": 25000, "limit": 10, "confirmed": True,
        "tier": "gold", "region": "eu-west-1",
    }
    assert redact_parameters(specs, values) == values


def test_sensitive_always_wins_over_an_opt_in():
    from aigenos_flow.connectors.manager import REDACTED, redact_parameters
    from aigenos_flow.connectors.models import ParameterSpec

    specs = {
        "api_key": ParameterSpec(type="string", log_value=True),
        "grade": ParameterSpec(type="string", enum=["a"], sensitive=True),
        "pin": ParameterSpec(type="integer", sensitive=True),
    }
    values = {"api_key": "sk-live-1", "grade": "a", "pin": 4321}
    assert redact_parameters(specs, values) == {
        "api_key": REDACTED, "grade": REDACTED, "pin": REDACTED,
    }


def test_an_undeclared_parameter_is_redacted_not_trusted():
    from aigenos_flow.connectors.manager import REDACTED, redact_parameters

    assert redact_parameters({}, {"surprise": "value"}) == {"surprise": REDACTED}


def test_an_absent_value_reads_as_absent_not_as_a_redacted_secret():
    from aigenos_flow.connectors.manager import redact_parameters
    from aigenos_flow.connectors.models import ParameterSpec

    specs = {"otp": ParameterSpec(type="string", sensitive=True)}
    assert redact_parameters(specs, {"otp": None}) == {"otp": None}


def test_runtime_audit_redacts_pii_end_to_end(tmp_path: Path):
    """Through manager.invoke() and the SQLite audit table, not just the helper."""
    from aigenos_flow.connectors.manager import REDACTED
    from aigenos_flow.connectors.models import ConnectorResult, InvocationContext

    class FakeAdapter:
        provider = "postgresql"
        def validate_operation(self, operation): return {"valid": True}
        def execute(self, uri, operation, parameters):
            return ConnectorResult(rows=[], row_count=0, execution_id="execution-2")
        def invalidate(self, connection_id): return None

    store = ConnectorStore(tmp_path / "audit2.sqlite")
    manager = ConnectorManager(store, SecretResolver("master"))
    manager.adapters["postgresql"] = FakeAdapter()
    connection = manager.create_connection({
        "provider": "postgresql", "name": "Payments", "access_level": "read",
        "credential": "postgresql://user:password@db/app?sslmode=verify-full",
    })
    operation = manager.prepare_operation(connection["id"], {
        "name": "charge_lookup", "display_name": "Charge lookup",
        "kind": "select", "mode": "read",
        "definition": {"sql": "not inspected by fake"},
        "parameters": {
            "card_number": {"type": "string"},
            "cvv": {"type": "string"},
            "amount": {"type": "number"},
        },
    })
    manager.commit_operation(operation, create=True)
    manager.invoke(
        operation.id,
        {"card_number": "4111111111111111", "cvv": "737", "amount": 42.5},
        context=InvocationContext(skill_name="charge", action_name="charge_lookup"),
    )
    audit = store.list_audits()[0]
    assert audit["parameters"] == {
        "card_number": REDACTED, "cvv": REDACTED, "amount": 42.5,
    }
    assert "4111111111111111" not in str(audit)
    assert "737" not in str(audit["parameters"])


def test_no_connector_response_ever_carries_the_credential(tmp_path: Path, monkeypatch):
    """Every read route, plus the audit trail after a real invocation."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from aigenos_flow.connectors.api import make_connector_router
    from aigenos_flow.connectors.models import ConnectorResult

    class FakeAdapter:
        provider = "postgresql"
        def validate_operation(self, operation): return {"valid": True}
        def execute(self, uri, operation, parameters):
            return ConnectorResult(rows=[], row_count=0, execution_id="execution-3")
        def invalidate(self, connection_id): return None

    secret = "tr0ub4dor-horse-battery"
    monkeypatch.setenv("FG_CONNECTOR_ADMIN_TOKEN", "admin-test-token")
    manager = ConnectorManager(
        ConnectorStore(tmp_path / "leak.sqlite"), SecretResolver("master")
    )
    manager.adapters["postgresql"] = FakeAdapter()
    app = FastAPI()
    app.include_router(make_connector_router(
        manager,
        validate_candidate=lambda registry: object(),
        apply_candidate=lambda skills, registry: None,
        skills_dir=str(tmp_path),
    ))
    client = TestClient(app)
    headers = {"x-connector-admin-token": "admin-test-token"}

    created = client.post("/connectors", headers=headers, json={
        "provider": "postgresql", "name": "Payments",
        "credential": f"postgresql://user:{secret}@db/app?sslmode=verify-full",
        "access_level": "read",
    })
    assert created.status_code == 201, created.text
    connection_id = created.json()["id"]

    operation = client.post(f"/connectors/{connection_id}/operations", headers=headers, json={
        "name": "card_lookup", "display_name": "Card lookup",
        "kind": "select", "mode": "read",
        "definition": {"sql": "SELECT 1 FROM cards WHERE number = %(card_number)s"},
        "parameters": {"card_number": {"type": "string"}},
        "timeout_seconds": 10, "row_limit": 100, "result_limit_bytes": 1_048_576,
        "enabled": True,
    })
    assert operation.status_code == 201, operation.text
    manager.invoke(operation.json()["id"], {"card_number": "4111111111111111"})

    for path in ("/connectors", "/connectors/providers", "/connectors/catalog",
                 f"/connectors/{connection_id}", "/connectors/audits"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        assert secret not in response.text, path
        assert "4111111111111111" not in response.text, path
