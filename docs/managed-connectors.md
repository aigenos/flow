# Managed database connectors

Managed connectors expose named PostgreSQL and MongoDB operations as typed tools. The model never receives a general query tool: an administrator defines the statement/template, typed inputs, limits, and access mode first; a declarative skill then binds validated fields to those inputs in Canvas.

## Configuration

Install the optional dependencies with:

```bash
pip install 'aigenos-flow[connectors]'
```

Set these backend variables:

```text
FG_CONNECTOR_STORE_PATH=/app/data/connectors.sqlite3
FG_CONNECTOR_ADMIN_TOKEN=<long random admin token>
FG_SKILL_WRITES=local
FG_CONNECTOR_MASTER_KEY=<32-byte base64 key or strong passphrase>
FG_CONNECTOR_AUDIT_RETENTION_DAYS=90
```

Docker Compose persists `/app/data` in the `connector-data` volume. The Canvas
binding step below does not work in that stack: Canvas requests reach the
backend from the container network, not from loopback, and are refused. Bind in
the local dev setup — and there, leave `FG_CONNECTOR_STORE_PATH` unset (the
store is then created at `./data/connectors.sqlite3` under the directory the
server starts in) or point it at a writable file: `/app/data/…` is the path
inside the image.

`FG_CONNECTOR_ADMIN_TOKEN` is **required**, not optional. Every
`/flow/connectors` endpoint depends on it:

- token unset → `503 connector management is disabled`
- token set, request missing or wrong → `401 invalid or expired connector admin token`
- otherwise → send it as `Authorization: Bearer <token>` or `X-Connector-Admin-Token: <token>`

`FG_SKILL_WRITES=local` is there because setting a token alone switches the
skill-write surface to token mode (the server reads `FG_ADMIN_TOKEN` **or**
`FG_CONNECTOR_ADMIN_TOKEN` as its management token), and the Studio does not
send that token on its Canvas requests, so the Canvas would stop loading. That
surface writes Python the engine imports: if you leave `FG_SKILL_WRITES=local`
out, treat this token as a code-execution credential, and either way keep the
port loopback-only unless you meant otherwise.

The Studio keeps the token in the browser tab's session storage only; it is
never persisted by the app.

A credential is either a complete URI entered once (encrypted with AES-256-GCM) or `env:VARIABLE_NAME`, where the environment variable contains the complete URI. APIs and edit forms return only the credential mode and whether it is configured; neither encrypted values, environment-variable names, nor resolved URIs are returned.

Verified TLS is the default: PostgreSQL URIs must set `sslmode=verify-full`, standard MongoDB URIs must set `tls=true`, and SRV keeps its verified-TLS default. Unverified or plain local connections are rejected unless `FG_CONNECTOR_ALLOW_INSECURE_LOCAL=true` is explicitly set for development.

## PostgreSQL operations

An operation contains exactly one `SELECT`, `INSERT`, `UPDATE`, or `DELETE`. Bind values with Psycopg's named syntax:

```sql
SELECT customer_id, name
FROM billing.customers
WHERE customer_id = %(customer_id)s
```

Every placeholder must have one typed input definition and every input must be used. Multiple statements, DDL, `CALL`, `COPY`, positional `%s`, and `UPDATE`/`DELETE` without `WHERE` are rejected. Reads use read-only transactions. Writes commit only on success and roll back errors.

## MongoDB operations

Supported kinds are `find_one`, `find_many`, `aggregate`, `insert_one`, `update_one`, `update_many`, `delete_one`, and `delete_many`. A parameter may replace only an exact JSON/EJSON value:

```json
{
  "collection": "customers",
  "filter": { "customer_id": "{{customer_id}}" }
}
```

Interpolation such as `"customer-{{customer_id}}"` is rejected. Mutation filters must be non-empty. `$where`, `$function`, `$accumulator`, `$out`, `$merge`, and generic database commands are unavailable.

## Skill binding and write safety

Open Studio → Connectors to define and validate an operation, then Studio → Canvas to bind it. Canvas updates `allowed-tools`, `actions`, typed fields, and flow steps as one server-validated SKILL.md change. Canvas saves through the skill-write endpoints, which with `FG_SKILL_WRITES=local` serve the machine running the server and nothing else (see Configuration above) — a request from another host answers 403 and nothing is written.

Reads can be deterministic `do:` actions or allowlisted `chat:` tools whose complete input set is pinned to fields. A managed read needs no `readonly-tools:` entry: the connector's own descriptor proves the operation is a read, and a managed write is refused in a `chat:`, `assist:` or `guidelines:` block whatever the skill declares. Any other tool one of those blocks uses must be named on the frontmatter's `readonly-tools:` line — the declared read-only subset of `allowed-tools` — or the skill does not load.

Managed writes can only be actions. They must:

- name a declared boolean confirmation field through `requires:`;
- run from a `do:` step guarded by a `when:` expression that proves that confirmation;
- keep `audit: true`;
- bind inputs from constrained typed fields — free text cannot reach a tool argument by any spelling, whole-argument, interpolated inside a larger string, or nested in a dict or list. Use a `pattern:` or an `enum`.

Writes are not automatically retried. The runtime's `effects_log` prevents a confirmed action from firing twice during one flow run, and it survives flow switches, digressions and cancellation. That is an at-most-once application guarantee under normal replay. Exactly-once behavior across a network timeout or process failure needs a database-enforced idempotency key; a gated action's `idempotency_key:` must include `{{flow_run_id}}`, which an operation can bind into such a unique key.

## Limits and auditing

Defaults are a 10-second execution timeout, 100 returned rows, and a 1 MB serialized result. Row limits are configurable up to 1,000. Results use a JSON-safe envelope:

```json
{
  "rows": [],
  "row_count": 0,
  "truncated": false,
  "affected_count": 1,
  "inserted_ids": [],
  "execution_id": "..."
}
```

Every runtime execution is audited in SQLite with operation/tool identifiers, skill/action, flow run, duration, status, counts, truncation, and parameters. Result bodies and credentials are never audited. Retention defaults to 90 days.

### What the audit trail keeps

Parameter values are a **default-deny allowlist**: a value is stored verbatim only when its declared type cannot carry a secret. Everything else becomes `***REDACTED***`.

| Parameter | Stored |
|---|---|
| `sensitive: true` | `***REDACTED***`, always, no exceptions |
| name matching `pass`/`secret`/`token`/`credential`/`api key`/`otp`/`ssn` | `***REDACTED***` — a belt, in case a `password` was declared as a plain string |
| a declared `enum` | the value: the domain is a closed set the admin wrote |
| `boolean` | the value: one bit, usually the gate the audit exists for |
| `integer` / `number` | the value: amounts and ids are the point of the trail |
| `string`, `object`, `array` | `***REDACTED***` unless that parameter sets `log_value: true` |
| not declared at all | `***REDACTED***` — nothing vouches for it |
| not supplied | `null`, so a reader can tell "a secret was passed" from "nothing was passed" |

A free string is redacted because nothing in its *name* has to reveal what it holds — `card_number`, `cvv`, `iban`, `email`, `dob`, `account_number` and `salary` all look like ordinary strings. `log_value: true` is the deliberate opt-out for an unconstrained parameter you have decided is safe to keep; `sensitive: true` still wins over it.

Given those rules, `status` (enum), `confirmed` (boolean), `amount_cents` (integer) and a `region` marked `log_value: true` come back as themselves, while `customer_id`, `iban`, `api_key`, a `sensitive` note and an undeclared key all come back as `***REDACTED***`.

V1 assumes one workspace (`workspace_id="default"`) and one backend process. Multi-user RBAC, multi-worker invalidation, SSH tunnels, IAM tokens, client certificates, custom CAs, and remote connector runners are deferred.
