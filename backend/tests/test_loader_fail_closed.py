"""The load-time verifier fails CLOSED.

One test per hole found in the pre-release audit of the loader. The product
claim these pin is narrow and total: *an unsafe skill is a load error before any
conversation starts*. Every test here mutates a skill that loads and gates
correctly (``CANONICAL``) into a shape that used to load in silence, and asserts
the loader now refuses it with a message that names the fix.

Design decisions these encode (owner-approved, see the audit):
  1. fail closed at load time on unknown/misspelled keys anywhere in SKILL.md
  2. every declared action is a WRITE unless it declares ``readonly: true``
  3. no silent downgrade of a typed skill to advisory prose
  4. gate inputs come from the user — including inline ``when:``/``check:``
  8. free text never reaches a tool argument, however the binding is written
  9. write tools are unreachable from conversational leaves
 10. a handlers.py ``@action`` write is held to the same gating rules
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, initial_state
from aigenos_flow.declarative import ToolRegistry
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import SetField
from aigenos_flow.skills import load_skill, load_skills_dir, parse_skill_md

CANONICAL = """\
---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000+ require a 2FA code.
allowed-tools: bank_transfer
fields:
  amount:    { type: number, min: 0.01, must_elicit: true }
  recipient: { type: enum, values: [mom, landlord, gym] }
  verification_code: { type: string, required: false, pattern: '^[0-9]{6}$', sensitive: true }
  confirm:   { type: boolean }
compute:
  requires_2fa: "amount >= 1000"
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    idempotency_key: "{{flow_run_id}}"
    args: { amount: "{{amount}}", payee: "{{recipient}}", otp: "{{verification_code}}" }
flow:
  - ask: amount
  - ask: recipient
  - when: "requires_2fa"
    ask: verification_code
  - say: "Send {{amount}} to {{recipient}}?"
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
---
Help the user transfer money.
"""

ADVISORY = """\
---
name: house-style
description: How we talk to customers.
---
Be brief. Never blame the customer. Escalate billing disputes to a human.
"""


def _skill(tmp_path: Path, md: str, *, name: str = "transfer-funds", handlers: str | None = None) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(md, encoding="utf-8")
    if handlers is not None:
        (d / "handlers.py").write_text(textwrap.dedent(handlers), encoding="utf-8")
    return d


def _registry() -> tuple[ToolRegistry, list[dict]]:
    fired: list[dict] = []

    def bank_transfer(args):
        """Move money."""
        fired.append(dict(args))
        return {"id": "TXN-1"}

    reg = ToolRegistry()
    reg.register("bank_transfer", bank_transfer)
    return reg, fired


def _fill(template: str, placeholder: str, value: str) -> str:
    """str.format() is unusable here: SKILL.md is full of YAML braces."""
    assert placeholder in template
    return template.replace(placeholder, value)


def PY_ACTION_SKILL_STEP(step: str) -> str:
    return _fill(PY_ACTION_SKILL, "#STEP#", step)


def _load(tmp_path: Path, md: str, **kw):
    reg, fired = _registry()
    return load_skill(_skill(tmp_path, md, **kw), tool_registry=reg), fired


# --------------------------------------------------------------------------- #
# The control: the canonical skill loads, and its gate actually holds.
# --------------------------------------------------------------------------- #
def test_canonical_skill_loads_and_gates(tmp_path):
    reg, fired = _registry()
    _skill(tmp_path / "skills", CANONICAL)
    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path / "skills", host=host, tools=reg)

    st = initial_state()
    st["flow_id"] = "transfer-funds"
    host.queue_commands(
        "send $5000 to mom",
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
    )
    st["pending_user_message"] = "send $5000 to mom"
    st = agent.turn(st)
    assert st["awaiting_field"] == "verification_code"
    assert fired == []


# --------------------------------------------------------------------------- #
# verifier-1 / hn-4 / decision 8 — free text may not reach a tool argument,
# however the binding is spelled.
# --------------------------------------------------------------------------- #
def test_unconstrained_string_whole_arg_is_load_error(tmp_path):
    md = CANONICAL.replace("pattern: '^[0-9]{6}$', ", "")
    with pytest.raises(ValueError, match="unconstrained string"):
        _load(tmp_path, md)


def test_unconstrained_string_interpolated_into_a_larger_arg_is_load_error(tmp_path):
    """`memo: "pay {{payee_note}} now"` used to load: the check only fired when
    the YAML value was exactly `{{name}}`."""
    md = CANONICAL.replace(
        "  confirm:   { type: boolean }",
        "  confirm:   { type: boolean }\n  payee_note: { type: string }",
    ).replace(
        'otp: "{{verification_code}}" }',
        'otp: "{{verification_code}}", memo: "pay {{payee_note}} now" }',
    )
    with pytest.raises(ValueError, match=r"binds 'payee_note' into tool arg 'memo'"):
        _load(tmp_path, md)


def test_compute_alias_cannot_launder_free_text_into_a_tool_arg(tmp_path):
    """A one-line `compute:` that is a bare field name inherits that field's
    (lack of) constraint — the alias was a way round the whole rule."""
    md = CANONICAL.replace(
        "  confirm:   { type: boolean }",
        "  confirm:   { type: boolean }\n  payee_note: { type: string, must_elicit: true }",
    ).replace(
        'compute:\n  requires_2fa: "amount >= 1000"',
        'compute:\n  requires_2fa: "amount >= 1000"\n  payee_alias: "payee_note"',
    ).replace(
        'payee: "{{recipient}}"', 'payee: "{{payee_alias}}"'
    )
    with pytest.raises(ValueError, match=r"binds 'payee_alias' into tool arg 'payee'"):
        _load(tmp_path, md)


def test_a_constrained_compute_alias_is_still_allowed(tmp_path):
    """The rule is about provenance, not about computes: an alias of an enum
    field is as safe as the field."""
    md = CANONICAL.replace(
        "  confirm:   { type: boolean }",
        "  confirm:   { type: boolean }\n"
        "  payee_ok: { type: enum, values: [mom, landlord, gym], must_elicit: true }",
    ).replace(
        'compute:\n  requires_2fa: "amount >= 1000"',
        'compute:\n  requires_2fa: "amount >= 1000"\n  payee_alias: "payee_ok"',
    ).replace('payee: "{{recipient}}"', 'payee: "{{payee_alias}}"')
    skill, _ = _load(tmp_path, md)
    assert "submit_transfer" in skill.actions


# --------------------------------------------------------------------------- #
# verifier-10 — nested arg values were never walked at load time AND never
# bound at runtime, so the literal "{{name}}" reached the tool.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("nested", [
    'details: { payee: "{{recipient}}" }',
    'tags: ["{{recipient}}"]',
    'details: { payee: "{{totally_undeclared}}" }',
])
def test_nested_arg_binding_is_load_error(tmp_path, nested):
    md = CANONICAL.replace(
        'otp: "{{verification_code}}" }', f'otp: "{{{{verification_code}}}}", {nested} }}'
    )
    with pytest.raises(ValueError, match="inside a nested structure"):
        _load(tmp_path, md)


def test_nested_literal_arg_without_bindings_still_loads(tmp_path):
    md = CANONICAL.replace(
        'otp: "{{verification_code}}" }',
        'otp: "{{verification_code}}", opts: { channel: "wire" } }',
    )
    skill, _ = _load(tmp_path, md)
    assert "submit_transfer" in skill.actions


def test_dotted_binding_in_a_tool_arg_is_load_error(tmp_path):
    md = CANONICAL.replace('payee: "{{recipient}}"', 'payee: "{{recipient.name}}"')
    with pytest.raises(ValueError, match="dotted binding"):
        _load(tmp_path, md)


# --------------------------------------------------------------------------- #
# verifier-2 / verifier-14 — one namespace, so collisions and reserved names
# are load errors instead of a silent decision about which value a gate reads.
# --------------------------------------------------------------------------- #
def test_compute_colliding_with_a_field_is_load_error(tmp_path):
    md = CANONICAL.replace(
        'compute:\n  requires_2fa: "amount >= 1000"',
        'compute:\n  requires_2fa: "amount >= 1000"\n  confirm: "amount > 0"',
    )
    with pytest.raises(ValueError, match=r"compute \['confirm'\] collide"):
        _load(tmp_path, md)


def test_declared_field_named_flow_run_id_is_load_error(tmp_path):
    md = CANONICAL.replace(
        "  confirm:   { type: boolean }",
        "  confirm:   { type: boolean }\n  flow_run_id: { type: string, pattern: '^INV-[0-9]{4}$' }",
    )
    with pytest.raises(ValueError, match="reserved name"):
        _load(tmp_path, md)


def test_action_name_colliding_with_a_field_is_load_error(tmp_path):
    md = CANONICAL.replace("  submit_transfer:", "  confirm:").replace(
        "    do: submit_transfer", "    do: confirm"
    )
    with pytest.raises(ValueError, match="collide with a declared field"):
        _load(tmp_path, md)


# --------------------------------------------------------------------------- #
# verifier-4 / verifier-12 / verifier-13 / decision 1 — the step vocabulary is
# closed, one verb per step, no empty verb.
# --------------------------------------------------------------------------- #
def test_misspelled_step_guard_is_load_error_naming_the_fix(tmp_path):
    """`wehn:` used to be ignored, which deleted the guard on the money step."""
    md = CANONICAL.replace('  - when: "confirm"\n    do: submit_transfer',
                           '  - wehn: "confirm"\n    do: submit_transfer')
    with pytest.raises(ValueError, match=r"unknown key 'wehn' \(did you mean 'when'\?\)"):
        _load(tmp_path, md)


def test_unknown_step_key_is_load_error(tmp_path):
    md = CANONICAL.replace("  - ask: recipient", "  - ask: recipient\n    totally_unknown: yes")
    with pytest.raises(ValueError, match="unknown key 'totally_unknown'"):
        _load(tmp_path, md)


def test_key_the_step_verb_never_reads_is_load_error(tmp_path):
    """A real key on the wrong verb is a silent no-op too: `prompt:` on an
    `ask:` step was simply dropped."""
    md = CANONICAL.replace("  - ask: recipient", '  - ask: recipient\n    prompt: "Who?"')
    with pytest.raises(ValueError, match=r"never reads 'prompt'"):
        _load(tmp_path, md)


def test_two_verbs_in_one_step_is_load_error(tmp_path):
    md = CANONICAL.replace(
        '  - when: "confirm"\n    do: submit_transfer',
        '  - when: "confirm"\n    check: "confirm"\n    else_say: "no"\n'
        "    else_clear: [confirm]\n    do: submit_transfer",
    )
    with pytest.raises(ValueError, match=r"carries 2 verbs \['check', 'do'\]"):
        _load(tmp_path, md)


def test_do_alongside_run_is_load_error(tmp_path):
    md = CANONICAL.replace("    do: submit_transfer",
                           "    do: submit_transfer\n    run: not_an_action")
    with pytest.raises(ValueError, match="carries 2 verbs"):
        _load(tmp_path, md)


def test_empty_do_is_load_error_not_a_runtime_keyerror(tmp_path):
    md = CANONICAL.replace("    do: submit_transfer", "    do:")
    with pytest.raises(ValueError, match="do: is empty"):
        _load(tmp_path, md)


def test_pick_with_a_catalog_widget_is_still_the_allowed_pair(tmp_path):
    md = """\
---
name: lookup
description: Pick a row.
allowed-tools: search_rows
fields:
  q: { type: string, must_elicit: true, pattern: '^[a-z]+$' }
  row_id: { type: string, pattern: '^R[0-9]+$' }
actions:
  search_rows: { tool: search_rows, readonly: true, args: { q: "{{q}}" } }
flow:
  - ask: q
  - do: search_rows
  - pick: "{{search_rows.rows}}"
    widget: row_picker
    save: [row_id]
---
Body.
"""
    reg = ToolRegistry()
    reg.register("search_rows", lambda a: {"rows": []})
    skill = load_skill(_skill(tmp_path, md, name="lookup"), tool_registry=reg)
    assert skill.name == "lookup"


# --------------------------------------------------------------------------- #
# verifier-5 / decision 1 — unknown keys in a field spec, an action spec, a
# guideline or the frontmatter itself.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad,good", [
    ("maximum: 1000", "max"),
    ("sensitve: true", "sensitive"),
    ("must_ellicit: true", "must_elicit"),
    ("patern: '^x$'", "pattern"),
    ("requried: false", "required"),
])
def test_misspelled_field_key_is_load_error_with_the_near_miss(tmp_path, bad, good):
    md = CANONICAL.replace("  recipient: { type: enum, values: [mom, landlord, gym] }",
                           f"  recipient: {{ type: enum, values: [mom, landlord, gym], {bad} }}")
    with pytest.raises(ValueError, match=rf"field 'recipient' has unknown key .*did you mean '{good}'"):
        _load(tmp_path, md)


@pytest.mark.parametrize("bad,good", [
    ("require: confirm", "requires"),
    ("idempotency_keys: x", "idempotency_key"),
    ("audits: false", "audit"),
    ("read_only: true", "readonly"),
])
def test_misspelled_action_key_is_load_error_with_the_near_miss(tmp_path, bad, good):
    md = CANONICAL.replace("    requires: confirm", f"    requires: confirm\n    {bad}")
    with pytest.raises(ValueError, match=rf"action 'submit_transfer' has unknown key .*did you mean '{good}'"):
        _load(tmp_path, md)


def test_unknown_top_level_frontmatter_key_is_load_error(tmp_path):
    md = CANONICAL.replace("compute:", "computes:")
    with pytest.raises(ValueError, match=r"unknown key 'computes' \(did you mean 'compute'\?\)"):
        _load(tmp_path, md)


def test_field_default_is_not_a_loadable_key(tmp_path):
    """`default:` parsed, was exported on FieldSpec, and nothing ever read it —
    an author could believe an unset field would be seeded."""
    md = CANONICAL.replace(
        "  confirm:   { type: boolean }",
        "  confirm:   { type: boolean, default: true }",
    )
    with pytest.raises(ValueError, match="unknown key 'default'"):
        _load(tmp_path, md)


# --------------------------------------------------------------------------- #
# hn-3 — a one-character typo in `flow:` used to demote a gated skill to plain
# prompt text and hand the write tool to the model.
# --------------------------------------------------------------------------- #
def test_typoed_flow_key_is_load_error_not_a_silent_downgrade(tmp_path):
    md = CANONICAL.replace("\nflow:\n", "\nflowz:\n")
    with pytest.raises(ValueError, match=r"unknown key 'flowz' \(did you mean 'flow'\?\)"):
        _load(tmp_path, md)


def test_parse_skill_md_reports_the_offending_file(tmp_path):
    with pytest.raises(ValueError, match="not loadable"):
        parse_skill_md(CANONICAL.replace("\nflow:\n", "\nflowz:\n"), source="SKILL.md")


# --------------------------------------------------------------------------- #
# verifier-8 — a typed policy that nothing runs, and blocks the chosen runtime
# never reads, are load errors rather than an evaporated policy.
# --------------------------------------------------------------------------- #
def test_typed_policy_with_no_runnable_flow_is_load_error(tmp_path):
    md = CANONICAL.replace(
        "\nflow:\n  - ask: amount\n  - ask: recipient\n"
        '  - when: "requires_2fa"\n    ask: verification_code\n'
        '  - say: "Send {{amount}} to {{recipient}}?"\n  - ask: confirm\n'
        '  - when: "confirm"\n    do: submit_transfer\n',
        "\n",
    )
    with pytest.raises(ValueError, match="no runnable flow"):
        _load(tmp_path, md)


def test_imperative_flow_declaring_actions_is_load_error(tmp_path):
    """The mixed-mode trap: a handlers.py @flow ignores `actions:`/`compute:`
    entirely, so the allowlist, the gates and the arg bindings the author wrote
    were never checked and never used."""
    md = CANONICAL.replace(
        "\nflow:\n  - ask: amount\n  - ask: recipient\n"
        '  - when: "requires_2fa"\n    ask: verification_code\n'
        '  - say: "Send {{amount}} to {{recipient}}?"\n  - ask: confirm\n'
        '  - when: "confirm"\n    do: submit_transfer\n',
        "\n",
    )
    handlers = """
        from aigenos_flow import flow

        @flow
        def transfer(ctx):
            ctx.ask("amount")
    """
    with pytest.raises(ValueError, match="never reads them"):
        _load(tmp_path, md, handlers=handlers)


# --------------------------------------------------------------------------- #
# verifier-9 — the guard must PROVE the confirmation, not merely mention it.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("guard", ['"not confirm"', '"confirm is not None"', '"amount > 0"'])
def test_guard_that_does_not_prove_the_confirmation_is_load_error(tmp_path, guard):
    md = CANONICAL.replace('  - when: "confirm"\n    do: submit_transfer',
                           f"  - when: {guard}\n    do: submit_transfer")
    with pytest.raises(ValueError, match="does not prove 'confirm'|not guarded"):
        _load(tmp_path, md)


@pytest.mark.parametrize("guard", ['"confirm"', '"confirm and amount > 0"', '"confirm == True"'])
def test_guard_that_proves_the_confirmation_loads(tmp_path, guard):
    md = CANONICAL.replace('  - when: "confirm"\n    do: submit_transfer',
                           f"  - when: {guard}\n    do: submit_transfer")
    skill, _ = _load(tmp_path, md)
    assert "submit_transfer" in skill.actions


def test_ungated_step_for_a_gated_action_is_load_error(tmp_path):
    md = CANONICAL.replace('  - when: "confirm"\n    do: submit_transfer',
                           "  - do: submit_transfer")
    with pytest.raises(ValueError, match="not guarded"):
        _load(tmp_path, md)


# --------------------------------------------------------------------------- #
# decision 2 — every declared action is a write unless it says otherwise.
# --------------------------------------------------------------------------- #
def test_action_without_requires_or_readonly_is_load_error(tmp_path):
    md = CANONICAL.replace("    requires: confirm\n", "").replace(
        '  - when: "confirm"\n    do: submit_transfer', "  - do: submit_transfer"
    )
    with pytest.raises(ValueError, match="is a write"):
        _load(tmp_path, md)


def test_readonly_action_needs_no_gate(tmp_path):
    md = """\
---
name: lookup
description: Look something up.
allowed-tools: search_rows
fields:
  q: { type: string, must_elicit: true, pattern: '^[a-z]+$' }
actions:
  search_rows:
    tool: search_rows
    readonly: true
    audit: false
    args: { q: "{{q}}" }
flow:
  - ask: q
  - do: search_rows
---
Body.
"""
    reg = ToolRegistry()
    reg.register("search_rows", lambda a: {"rows": []})
    skill = load_skill(_skill(tmp_path, md, name="lookup"), tool_registry=reg)
    assert "search_rows" in skill.actions


def test_readonly_action_with_requires_is_load_error(tmp_path):
    md = CANONICAL.replace("    requires: confirm", "    requires: confirm\n    readonly: true")
    with pytest.raises(ValueError, match="no side effect to gate"):
        _load(tmp_path, md)


def test_a_tool_cannot_be_both_a_read_and_a_write_in_one_skill(tmp_path):
    """Otherwise a second, `readonly: true` action over the same tool reaches the
    side effect with no gate at all."""
    md = CANONICAL.replace(
        "actions:\n  submit_transfer:",
        "actions:\n"
        "  peek_transfer:\n"
        "    tool: bank_transfer\n"
        "    readonly: true\n"
        '    args: { amount: "{{amount}}" }\n'
        "  submit_transfer:",
    )
    with pytest.raises(ValueError, match="back BOTH a write action and a"):
        _load(tmp_path, md)


def test_readonly_tools_cannot_name_a_tool_this_skill_writes_with(tmp_path):
    md = CANONICAL.replace("allowed-tools: bank_transfer",
                           "allowed-tools: bank_transfer\nreadonly-tools: bank_transfer")
    with pytest.raises(ValueError, match="also uses as a write action"):
        _load(tmp_path, md)


def test_write_action_cannot_disable_auditing(tmp_path):
    md = CANONICAL.replace("    requires: confirm", "    requires: confirm\n    audit: false")
    with pytest.raises(ValueError, match="cannot disable auditing"):
        _load(tmp_path, md)


# --------------------------------------------------------------------------- #
# verifier-7 — at-most-once per flow run is a property of the engine, not of
# one spelling of idempotency_key.
# --------------------------------------------------------------------------- #
def test_business_idempotency_key_without_flow_run_id_is_load_error(tmp_path):
    md = CANONICAL.replace('idempotency_key: "{{flow_run_id}}"',
                           'idempotency_key: "{{recipient}}-{{amount}}"')
    with pytest.raises(ValueError, match="does not include"):
        _load(tmp_path, md)


def test_idempotency_key_may_narrow_but_must_include_the_run_id(tmp_path):
    md = CANONICAL.replace('idempotency_key: "{{flow_run_id}}"',
                           'idempotency_key: "{{flow_run_id}}-{{recipient}}"')
    skill, _ = _load(tmp_path, md)
    assert "submit_transfer" in skill.actions


# --------------------------------------------------------------------------- #
# verifier-6 / verifier-16 — every gate expression is proven evaluable at load
# time, and a bad one names the skill and the block.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("expr,needle", [
    ('"attendees * per_head"', "BinOp"),
    ('"(lambda: True)()"', "Call"),
    ('"amount.__class__"', "dunder"),
])
def test_unevaluable_compute_is_load_error_not_a_first_turn_crash(tmp_path, expr, needle):
    md = CANONICAL.replace('requires_2fa: "amount >= 1000"', f"requires_2fa: {expr}")
    with pytest.raises(ValueError, match=needle):
        _load(tmp_path, md)


def test_unevaluable_step_condition_is_load_error(tmp_path):
    md = CANONICAL.replace('  - when: "confirm"\n    do: submit_transfer',
                           '  - when: "confirm and f\'{amount}\' == \'1\'"\n    do: submit_transfer')
    with pytest.raises(ValueError, match="JoinedStr"):
        _load(tmp_path, md)


def test_syntax_error_in_compute_names_the_skill_and_the_block(tmp_path):
    md = CANONICAL.replace('requires_2fa: "amount >= 1000"', 'requires_2fa: "amount >"')
    with pytest.raises(ValueError, match=r"compute 'requires_2fa' is not a valid expression"):
        _load(tmp_path, md)


# --------------------------------------------------------------------------- #
# verifier-17 / KNOWN-2 / decision 4 — the gate-input rule follows the gate,
# not the place the author wrote it.
# --------------------------------------------------------------------------- #
# These three used to be load errors ("field 'amount' feeds gate ..., so it must
# set `must_elicit: true`"). The loader knew exactly which field needed the mark
# and made the author type it. It now sets the mark itself: same guarantee, one
# keyword fewer to learn, and nothing to forget.
UNMARKED = CANONICAL.replace("  amount:    { type: number, min: 0.01, must_elicit: true }",
                             "  amount:    { type: number, min: 0.01 }")


def test_a_field_feeding_a_compute_gate_is_user_only_unmarked(tmp_path):
    assert "must_elicit" not in UNMARKED
    skill, _ = _load(tmp_path, UNMARKED)
    assert skill.fields["amount"].must_elicit is True
    assert skill.fields["recipient"].must_elicit is False  # no gate reads it


def test_a_field_feeding_an_inline_when_is_user_only_unmarked(tmp_path):
    md = UNMARKED.replace(
        'compute:\n  requires_2fa: "amount >= 1000"\n', ""
    ).replace('  - when: "requires_2fa"', '  - when: "amount >= 1000"')
    skill, _ = _load(tmp_path, md)
    assert skill.fields["amount"].must_elicit is True


def test_a_field_feeding_an_inline_check_is_user_only_unmarked(tmp_path):
    md = UNMARKED.replace(
        'compute:\n  requires_2fa: "amount >= 1000"\n', ""
    ).replace(
        '  - when: "requires_2fa"\n    ask: verification_code',
        '  - check: "amount <= 25000"\n    else_say: "Too much."\n    else_clear: [amount]',
    )
    skill, _ = _load(tmp_path, md)
    assert skill.fields["amount"].must_elicit is True


def test_an_unmarked_gate_input_cannot_be_supplied_by_the_model(tmp_path):
    """The point of the mark, on the file that no longer carries it: the model
    says the user wants $500, the user said no number, and the 2FA gate must
    not be decided by a value nobody typed."""
    reg, fired = _registry()
    _skill(tmp_path / "skills", UNMARKED)
    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path / "skills", host=host, tools=reg)
    host.queue_commands(
        "send some money to mom",
        SetField(field="amount", value=500), SetField(field="recipient", value="mom"),
    )
    chat = agent.session("transfer-funds").user("send some money to mom")
    assert chat.state["fields"].get("amount") is None, "a number the user never said was taken"
    assert chat.state["fields"].get("recipient") == "mom"
    assert chat.awaiting == "amount" and not fired


def test_a_confirmation_boolean_does_not_need_must_elicit(tmp_path):
    """`when: "confirm"` gates on the action's own `requires:` field. Demanding
    `must_elicit: true` there too would be noise on every confirm field — but
    the value must still come from the user, so the loader publishes the set for
    the interpreter's backstop to use (decision 4)."""
    skill, _ = _load(tmp_path, CANONICAL)
    assert skill.fields["confirm"].must_elicit is False
    assert skill.confirmation_fields == ["confirm"]


def test_py_action_requires_is_published_as_a_confirmation_field(tmp_path):
    handlers = """
        from aigenos_flow import action

        @action(requires="confirm")
        def submit_transfer(state):
            return {"id": "PY-1"}
    """
    skill, _ = _load(tmp_path, PY_ACTION_SKILL_STEP(
        '  - when: "confirm"\n    do: submit_transfer'), handlers=handlers)
    assert skill.confirmation_fields == ["confirm"]


# --------------------------------------------------------------------------- #
# verifier-3 / decision 9 — conversational leaves may only reach tools proven
# read-only, however the tool was registered.
# --------------------------------------------------------------------------- #
CHAT_LEAF = """\
---
name: transfer-funds
description: Send money, then chat.
allowed-tools: bank_transfer
#READONLY#fields:
  amount: { type: number, must_elicit: true }
flow:
  - ask: amount
  - chat:
      tools: [bank_transfer]
      greeting: "Anything else?"
---
Body.
"""


def test_plain_write_tool_in_chat_is_load_error(tmp_path):
    with pytest.raises(ValueError, match="not declared read-only"):
        _load(tmp_path, _fill(CHAT_LEAF, "#READONLY#", ""))


def test_declared_readonly_tool_in_chat_loads(tmp_path):
    skill, _ = _load(tmp_path, _fill(CHAT_LEAF, "#READONLY#",
                                     "readonly-tools: bank_transfer\n"))
    assert set(skill.chat_tools) == {"bank_transfer"}


def test_readonly_tools_must_be_a_subset_of_allowed_tools(tmp_path):
    md = _fill(CHAT_LEAF, "#READONLY#", "readonly-tools: other_tool\n")
    with pytest.raises(ValueError, match="does not grant"):
        _load(tmp_path, md)


def test_plain_write_tool_in_assist_is_load_error(tmp_path):
    md = """\
---
name: transfer-funds
description: Send money.
allowed-tools: bank_transfer
assist:
  tools: [bank_transfer]
fields:
  amount: { type: number, must_elicit: true }
flow:
  - ask: amount
---
Body.
"""
    with pytest.raises(ValueError, match="not declared read-only"):
        _load(tmp_path, md)


def test_plain_write_tool_in_guidelines_is_load_error(tmp_path):
    md = """\
---
name: transfer-funds
description: Send money.
allowed-tools: bank_transfer
guidelines:
  - when: the user asks to send money
    then: offer to move it
    tools: [bank_transfer]
fields:
  amount: { type: number, must_elicit: true }
flow:
  - ask: amount
---
Body.
"""
    with pytest.raises(ValueError, match="not declared read-only"):
        _load(tmp_path, md)


def test_chat_pin_of_an_unconstrained_field_is_load_error(tmp_path):
    """A `pin:` is a tool argument the ENGINE fills, so decision 8 applies to it
    exactly as it applies to an action's `args:`."""
    md = _fill(CHAT_LEAF, "#READONLY#", "readonly-tools: bank_transfer\n").replace(
        "  amount: { type: number, must_elicit: true }",
        "  amount: { type: number, must_elicit: true }\n  note: { type: string }",
    ).replace(
        "      greeting:", "      pin: { memo: note }\n      greeting:"
    )
    with pytest.raises(ValueError, match=r"chat pin 'memo' pins field 'note'"):
        _load(tmp_path, md)


def test_chat_tools_must_be_a_list(tmp_path):
    """`tools: bank_transfer` used to be iterated character by character."""
    md = _fill(CHAT_LEAF, "#READONLY#", "readonly-tools: bank_transfer\n").replace(
        "      tools: [bank_transfer]", "      tools: bank_transfer"
    )
    with pytest.raises(ValueError, match="tools: must be a list"):
        _load(tmp_path, md)


def test_unknown_key_in_a_chat_leaf_is_load_error(tmp_path):
    md = _fill(CHAT_LEAF, "#READONLY#", "readonly-tools: bank_transfer\n").replace(
        "      greeting:", "      greetings:"
    )
    with pytest.raises(ValueError, match=r"unknown key 'greetings' \(did you mean 'greeting'\?\)"):
        _load(tmp_path, md)


# --------------------------------------------------------------------------- #
# verifier-11 / decision 10 — a handlers.py @action write is gated like any
# other write.
# --------------------------------------------------------------------------- #
PY_ACTION_SKILL = """\
---
name: transfer-funds
description: Send money via a Python action.
fields:
  amount:  { type: number, must_elicit: true }
  confirm: { type: boolean }
flow:
  - ask: amount
  - ask: confirm
#STEP#
---
Body.
"""


def test_ungated_py_action_write_is_load_error(tmp_path):
    handlers = """
        from aigenos_flow import action

        @action
        def submit_transfer(state):
            return {"id": "PY-1"}
    """
    with pytest.raises(ValueError, match=r"every @action is, unless it declares"):
        _load(tmp_path, PY_ACTION_SKILL_STEP("  - do: submit_transfer"),
              handlers=handlers)


def test_py_action_write_needs_a_guard_that_proves_its_requires(tmp_path):
    handlers = """
        from aigenos_flow import action

        @action(requires="confirm")
        def submit_transfer(state):
            return {"id": "PY-1"}
    """
    with pytest.raises(ValueError, match="does not prove 'confirm'"):
        _load(tmp_path, PY_ACTION_SKILL_STEP('  - when: "not confirm"\n    do: submit_transfer'), handlers=handlers)


def test_gated_py_action_write_loads(tmp_path):
    handlers = """
        from aigenos_flow import action

        @action(requires="confirm")
        def submit_transfer(state):
            return {"id": "PY-1"}
    """
    skill, _ = _load(tmp_path, PY_ACTION_SKILL_STEP('  - when: "confirm"\n    do: submit_transfer'), handlers=handlers)
    assert "submit_transfer" in skill.actions


def test_readonly_py_action_needs_no_gate(tmp_path):
    handlers = """
        from aigenos_flow import action

        @action(readonly=True)
        def lookup_rate(state):
            return {"rate": 1.0}
    """
    skill, _ = _load(tmp_path, PY_ACTION_SKILL_STEP("  - do: lookup_rate"),
                     handlers=handlers)
    assert "lookup_rate" in skill.actions


def test_py_action_cannot_be_both_readonly_and_gated():
    from aigenos_flow import action

    with pytest.raises(ValueError, match="cannot also declare requires"):
        @action(readonly=True, requires="confirm")
        def bad(state):  # pragma: no cover - the decorator raises
            return None


# --------------------------------------------------------------------------- #
# hn-9 / decision 3 — advisory skills load; a broken sibling does not take the
# folder down; a typed skill is never downgraded to prose.
# --------------------------------------------------------------------------- #
def test_instruction_only_skill_loads_as_advisory(tmp_path):
    skill = load_skill(_skill(tmp_path, ADVISORY, name="house-style"))
    assert skill.flow_fn is None
    assert skill.workflow is False       # never routed to by the deterministic policy
    assert "Escalate billing disputes" in skill.body


def test_workflow_false_opts_a_skill_out(tmp_path):
    md = ADVISORY.replace("---\nBe brief.", "workflow: false\n---\nBe brief.")
    skill = load_skill(_skill(tmp_path, md, name="house-style"))
    assert skill.workflow is False


def test_workflow_false_with_a_typed_policy_is_load_error(tmp_path):
    md = CANONICAL.replace("allowed-tools: bank_transfer",
                           "allowed-tools: bank_transfer\nworkflow: false")
    with pytest.raises(ValueError, match="workflow: false"):
        _load(tmp_path, md)


def test_workflow_false_with_a_handlers_file_is_load_error(tmp_path):
    """The opt-out and a Python @flow contradict each other; honouring one half
    in silence is how a runnable skill becomes unreachable prose (or worse)."""
    md = ADVISORY.replace("---\nBe brief.", "workflow: false\n---\nBe brief.")
    handlers = """
        from aigenos_flow import flow

        @flow
        def style(ctx):
            ctx.say("hi")
    """
    with pytest.raises(ValueError, match="workflow: false"):
        load_skill(_skill(tmp_path, md, name="house-style", handlers=handlers))


def test_mixed_folder_loads_both_kinds(tmp_path):
    reg, _ = _registry()
    root = tmp_path / "skills"
    _skill(root, CANONICAL)
    _skill(root, ADVISORY, name="house-style")
    registry = load_skills_dir(root, tool_registry=reg)
    assert sorted(s.name for s in registry.all()) == ["house-style", "transfer-funds"]
    assert registry.errors == {}


def test_one_broken_skill_does_not_take_the_folder_down(tmp_path, caplog):
    reg, _ = _registry()
    root = tmp_path / "skills"
    _skill(root, CANONICAL)
    _skill(root, ADVISORY, name="house-style")
    _skill(root, CANONICAL.replace("name: transfer-funds", "name: broken-skill")
           .replace('  - when: "confirm"\n    do: submit_transfer', "  - do: submit_transfer"),
           name="broken-skill")

    registry = load_skills_dir(root, tool_registry=reg)
    assert sorted(s.name for s in registry.all()) == ["house-style", "transfer-funds"]
    assert "broken-skill" in registry.errors
    assert "not guarded" in registry.errors["broken-skill"]
    assert "broken-skill" in caplog.text


def test_strict_loading_re_raises_the_first_failure(tmp_path):
    reg, _ = _registry()
    root = tmp_path / "skills"
    _skill(root, CANONICAL)
    _skill(root, CANONICAL.replace("name: transfer-funds", "name: broken-skill")
           .replace('  - when: "confirm"\n    do: submit_transfer', "  - do: submit_transfer"),
           name="broken-skill")
    with pytest.raises(ValueError, match="not guarded"):
        load_skills_dir(root, tool_registry=reg, strict=True)


def test_a_folder_where_nothing_loads_is_an_error(tmp_path):
    reg, _ = _registry()
    root = tmp_path / "skills"
    _skill(root, CANONICAL.replace('  - when: "confirm"\n    do: submit_transfer',
                                   "  - do: submit_transfer"))
    with pytest.raises(ValueError, match="no skill in"):
        load_skills_dir(root, tool_registry=reg)
