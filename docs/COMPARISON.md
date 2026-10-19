# How aigenos/flow compares

**Scope & honesty note.** This compares *architecture classes* for one job:
LLM-grade conversation over multi-step business workflows where policy must
hold. It deliberately names no products — tools evolve fast, and class-level
trade-offs age better than point-in-time feature tables. It also quotes no
measured rates, ours or anyone else's: the arguments below are about what a
design can guarantee, which is the part that does not change between runs.
Corrections welcome via issue or PR.

The one-sentence positioning: **aigenos/flow is deterministic flows with a2ui
widgets over the AG-UI communication layer, driven by SKILL.md skills in the
Agent Skills layout — Apache-2.0, LangGraph-native, with a policy surface that is verified
before any conversation starts.** The "LLM interprets, deterministic engine
decides" split has prior art in both academia and commercial conversational AI
(see [Academic neighbors](#academic-neighbors)); what we found no other open
implementation of is that split **with generative UI as flow steps** and a
load-time-verified policy surface.

---

## The four architecture classes

### 1. Prompt-guardrailed agents (ReAct + policy text)

The policy lives in the system prompt and tool docstrings; whether it holds is
decided by sampled tokens, every turn. This is the fastest thing to ship, and
for a lot of work it is the right answer.

The structural limit is not that it fails often — on a strong model it may not
fail at all in the episodes you try. It is that a clean run is an *observation
about those episodes*. It does not transfer to the next phrasing, the next
temperature, or the next model you swap in, and nothing in the artifact tells
you which of the three you are now relying on. And the failure mode a design in
this class has to survive is not the obvious one — a skipped tool call — but an
**invented input**: a model asked for a verification code it does not have can
answer with something code-shaped instead of saying it cannot proceed, and the
call then looks compliant. That is the case aigenos/flow closes structurally
(question 5 below), not the case of a model forgetting to ask.

### 2. Guardrail / guideline middleware

A supervising layer matches natural-language guidelines or rails to the
conversation and shapes what enters the model's context — genuinely useful
defense-in-depth, and something aigenos/flow composes with rather than
replaces. The structural limit of the class: *matching and adherence are
themselves model-adjudicated*. Guideline-following is reinforced, not
unreachable, so published results in this class take the form of an adherence
*rate* (see, e.g., Attentive Reasoning Queries,
[arXiv:2503.03669](https://arxiv.org/abs/2503.03669)). A high rate is a good
number for tone and behavior shaping. It is not a number you can certify a
payment gate on: the residual is precisely the compliance question.
Flow-shaped constructs in this class are typically advisory too — the agent may
skip or reorder states.

aigenos/flow has this class *inside* it, and keeps the split explicit:
`guidelines:` are natural-language rules the model follows, so they can steer
wording and suggest a read; `flow:`, `actions:` and gates are engine-enforced.
A guideline can never fire a side effect, and the loader proves it — a
guideline naming a tool that is not declared read-only refuses to load.

### 3. Agent orchestrators (graph runtimes)

Checkpointed graphs, human-in-the-loop interrupts, and deterministic routing
*if you write it*. This is substrate, not competition — **aigenos/flow compiles
onto LangGraph** (`as_langgraph()`), and the honest comparison is "orchestrator
alone" vs "orchestrator + a policy runtime." What the orchestrator class does
not give you by itself: a declarative policy surface that can be *verified at
load time*, correction semantics that re-arm downstream gates, effect
idempotency across re-runs (graph nodes re-execute after interrupts; keeping
side effects safe is left to your discipline), or slot collection that doesn't
route back through the model's judgment.

### 4. Conversational-AI platforms and visual bot builders

The classic platforms get determinism right — a state machine owns the
dialogue — and that discipline is the direct ancestor of this project. The
trade is the platform itself: a proprietary flow DSL or hosted builder, a
train/compile step between edit and test, your own runtime processes to
operate, and a frontend you typically bring yourself. aigenos/flow's bet is
that the same guarantees can live in a `pip install` library that embeds in the
agent stack you already have, with skills as single markdown files and widgets
as flow steps.

---

## Academic neighbors

The interpreter/program split is the classical architecture of task-oriented
dialogue, recently re-instantiated with LLM command generation. We claim the
guarantee set, not the split. Two adjacent research threads worth reading:
FlowAgent ([arXiv:2502.14345](https://arxiv.org/abs/2502.14345) — a research
system, unrelated to this project's `FlowAgent` class) and
"Blueprint First, Model Second"
([arXiv:2508.02721](https://arxiv.org/abs/2508.02721)) both pursue workflow
compliance with controller/blueprint mechanisms — evidence that the research
community considers prompt-only compliance insufficient.

---

## The rubric we'd want used on us

Rather than scoring other people's products, here are the questions we think
you should put to **any** framework in this space — including this one. For
aigenos/flow, each answer names the mechanism and the test that pins it, both
in this repo.

1. **Is the policy executed by a non-LLM engine?** Here: gates are replayed
   conditions evaluated by `backend/aigenos_flow/engine/safe_eval.py`, which fails closed on an
   unknown name; the model emits typed commands and never control flow.
   → `backend/tests/test_safe_eval_strict.py`, `backend/tests/flow/test_executor.py`
2. **Does a mid-conversation correction re-arm downstream gates?** Here: yes,
   by replay-from-top — raising `$500` to `$5000` re-opens the 2FA gate, and it
   also discards a code already collected and every memoized action result
   derived from the corrected field.
   → `test_raising_the_amount_after_2fa_re_asks_for_a_fresh_code`,
   `test_correction_clears_every_memo_even_with_depends_on` in
   `backend/tests/test_gate_runtime.py`
3. **Are side effects at-most-once across corrections and re-runs?** Here: an
   effects log keyed by `flow_run_id`, checkpointed with the rest of the state,
   so one confirmed intent fires one side effect even across a flow switch, a
   digression or a cancellation.
   → `test_one_confirmed_transfer_survives_a_flow_switch`,
   `test_effects_log_is_carried_over_not_wiped_by_a_new_flow`,
   `test_cancelling_does_not_erase_what_already_fired` in the same file
4. **Is the policy verified before any conversation starts?** Here: load-time
   fail-closed verification. An ungated write, a gate over an undeclared name,
   an unconstrained string bound into a tool argument, arithmetic in a
   `compute:`, a misspelled key anywhere in the file — all load errors, each
   naming its fix.
   → `backend/tests/test_loader_fail_closed.py`
5. **Can the model invent a gate input?** Here: not one the user never said. A
   `must_elicit` field, and any boolean an action names in `requires:`, drops a
   value that is not grounded in the user's own words — matched by whole token,
   not substring. NaN and infinities are refused at the type boundary, because
   they used to satisfy `min:` and switch a threshold gate off.
   **The honest limit:** grounding proves provenance, not shape. A word the user
   *did* say — lifted out of "skip the code" — is grounded, so a gate input also
   needs a `pattern:` or an enum for the runtime to reject it. That is enforced
   for anything bound into a tool argument and is the authoring rule we lead with
   in the [cookbook](skill-authoring.md#must_elicit-is-not-enough-on-its-own-give-the-field-a-shape).
   → `backend/tests/flow/test_must_elicit.py`, and
   `test_model_cannot_fill_the_2fa_field`,
   `test_model_cannot_answer_its_own_confirm_question`,
   `test_grounding_is_token_exact_for_numbers`,
   `test_inline_step_gate_input_is_also_user_only` in
   `backend/tests/test_gate_runtime.py`
6. **Do UI submissions bypass the model?** Here: a widget submission is a
   validated payload applied in pure Python; keys that are not declared fields
   of the active skill are dropped, and no LLM sees it.
   → `backend/tests/flow/test_widget_submission_bypass.py`,
   `test_submission_cannot_write_undeclared_field_names` in
   `backend/tests/test_gate_runtime.py`
7. **Is the evidence open?** Yes, with a caveat. The eight properties — seven
   checked at load, one (at-most-once) at runtime — are computable per skill:
   `aigenos_flow.guarantees.analyze_markdown(text)` reports each one as `holds`
   or `n/a` for *your* SKILL.md, and `n/a` means the property has nothing to
   bite on, not that it passed. The live-model comparison is open too:
   [`compare.py`](../examples/studio_agent/proofs/conference_trip/compare.py) runs the same twelve tools with and
   without the flow, and every run it counted is in
   [`observed/`](../examples/studio_agent/proofs/conference_trip/observed). It is one conversation on two models from
   one vendor, so treat the properties as the claim and the comparison as an
   example of what they change.

If another framework answers these well, use it with our blessing — the point
of the rubric is that "the model usually complies" is not an answer to any of
the seven.
