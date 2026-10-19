# Changelog

## 0.3.1 — first public release

The first version published to PyPI and npm. Earlier version numbers were
internal builds; the two notes in the docs about names that changed "since the
pre-release builds" refer to those.

What is in it:

- **The engine** (`pip install aigenos-flow`): a skill is a `SKILL.md` whose
  `steps:` are a list of five kinds of step (`ask`, `say`, `do`, `confirm`,
  `chat`), with `when:` for a condition, `check:` for what an answer must
  satisfy and `repeat:` for a list of items: 20 words in all, on
  [one page](docs/steps.md). No schema block and no Python
  in the skill. Types, lookups and who may give an answer are worked out from
  the steps and from the tools' signatures.
- **Tools are plain functions in one shared folder** (`skills/tools/`). A
  function marked `@readonly` is a lookup; any other needs a `confirm:` above
  it on every path that reaches it, runs at most once per flow run and is
  audited. Tools can also come from your code (`tools=`), from one skill's own
  `scripts.py`, or from an MCP server (`aigenos_flow.mcp_tools.MCPTools`,
  started locally or reached by URL). Tools loaded with LangChain's MCP
  adapters and `async def` tools work as they are, and `save_request` is built
  in.
- **A fail-closed loader.** A misspelled word, a tool that changes something
  with no yes above it, an input nothing asks for, a condition that compares
  text with a number: each is a load error with a sentence that says what to
  change, before any conversation starts.
- **What you approve is what runs.** A confirmation that releases a tool is a
  card built by the engine: the tool and its inputs. They are compared again
  when the tool is called; a difference means nothing runs and the yes is asked
  for again.
- **A yes from someone else.** `by: manager` on a `confirm:` files a request
  and waits. That yes cannot arrive through the conversation, from the user,
  the model or a posted form.
- **A skill reads back as a procedure.**
  `python -m aigenos_flow.procedure skills/ <skill>` prints each step in plain
  words, every way through the flow, and what must be true before each tool
  that changes something. No model is involved.
- **The runtime**: replay from the top on every turn, so a corrected answer
  applies every rule below it again; at-most-once writes; a flow hands off to
  another skill with `do: other-skill`. A cancelled flow is ended, not
  forgotten: what it collected is on record for the model to answer from,
  "continue" picks it up without its code or its yes, and "start over" and
  "let me pick a different one" each take one message. `wrap_agent` and
  `create_agent` for LangGraph.
- **The long form** (`fields:` / `actions:` / `flow:`, nine step kinds) is what
  `steps:` expands to, and stays available for free-form chat inside a flow and
  per-argument bindings. A Python `@flow` is the escape hatch for loops.
- **The widget server** (`pip install "aigenos-flow[server]"`): FastAPI routers
  that stream typed widgets over AG-UI, A2UI and MCP-UI, and the backend the
  Studio runs against.
- **The React renderer** (`npm install @aigenos/flow`): `<Chat>`, `useFlowChat`,
  `<Widget>` and 16 primitives.
- **The scaffold** (`npx aigenos-agentic-app my-app`).
- **The Studio**, a local single-user authoring tool that runs from a clone: a
  canvas where a skill is a column of steps checked by the loader as you edit
  (ten things to add, from a question to a repeat, someone else's yes or a
  chat), a Gallery of three templates written as steps, a Tools page, the
  Procedure view, an Approvals inbox, a widget designer, and a Playground that
  runs any skill over AG-UI, A2UI or MCP-UI and shows every frame it sends. A
  skill can be created from a sentence and changed from one; in both cases the
  model writes and the loader decides whether the result is valid.

Known limits are listed in the README under "Honest limitations" and "Not built
yet".
