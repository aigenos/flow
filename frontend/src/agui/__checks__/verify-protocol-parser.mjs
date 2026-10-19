/**
 * Executable regression check for the wire contract's JS half.
 *
 * Asserts that ../protocol.ts turns a widget pause in EACH advertised protocol
 * into the same sink calls, using SSE frames captured from the real routers
 * (backend/tests/test_wire_contract.py regenerates equivalents on every run).
 *
 * Before the shared parser existed, `useFlowChat({protocol: "a2ui"})` and
 * `{protocol: "mcp-ui"}` produced an empty transcript while the README
 * advertised all three — this check fails if that regresses.
 *
 * Run (node 22+, no dependencies, no build step):
 *   node --experimental-strip-types src/agui/__checks__/verify-protocol-parser.mjs
 *
 * It is .mjs on purpose: tsconfig's `include: ["src"]` and tailwind's
 * `./src/**\/*.{ts,tsx}` glob both skip it, and package.json's `files`
 * allowlist keeps everything under src/ out of the published tarball.
 */
const { handleProtocolFrame, PROTOCOLS, eventTypesFor } = await import("../protocol.ts");

const SPEC = {
  type: "card",
  props: { title: "Please provide a value for 'verification_code'." },
  children: [
    {
      type: "form",
      props: { submitLabel: "Continue" },
      children: [
        {
          type: "input",
          props: {
            name: "verification_code",
            label: "Verification Code",
            type: "text",
            skill: "transfer-funds",
          },
        },
      ],
    },
  ],
};
const PAYLOAD = {
  widget_id: "flow_transfer-funds",
  version: "1.0.0",
  spec: SPEC,
  submit_schema: { type: "object", properties: { verification_code: { type: "string" } } },
};
const SUBMISSION = { verification_code: "481920" };

/** One pause-then-resume turn pair, per protocol, as (event, data) frames. */
const FIXTURES = {
  "ag-ui": [
    ["RUN_STARTED", { type: "RUN_STARTED", thread_id: "T1", run_id: "R1" }],
    ["TOOL_CALL_RESULT", { type: "TOOL_CALL_RESULT", tool_call_id: "tc0", message_id: "m0", content: JSON.stringify(SUBMISSION) }],
    ["TEXT_MESSAGE_START", { type: "TEXT_MESSAGE_START", message_id: "m1" }],
    ["TEXT_MESSAGE_CONTENT", { type: "TEXT_MESSAGE_CONTENT", message_id: "m1", delta: "Got it." }],
    ["TEXT_MESSAGE_END", { type: "TEXT_MESSAGE_END", message_id: "m1" }],
    ["TOOL_CALL_START", { type: "TOOL_CALL_START", tool_call_id: "tc1", tool_call_name: "render_widget" }],
    ["TOOL_CALL_ARGS", { type: "TOOL_CALL_ARGS", tool_call_id: "tc1", delta: JSON.stringify(PAYLOAD) }],
    ["TOOL_CALL_END", { type: "TOOL_CALL_END", tool_call_id: "tc1" }],
    ["STATE_SNAPSHOT", { type: "STATE_SNAPSHOT", snapshot: { status: "awaiting_input", done: false } }],
    ["RUN_FINISHED", { type: "RUN_FINISHED", thread_id: "T1", run_id: "R1" }],
  ],
  a2ui: [
    ["a2ui.run.start", { thread_id: "T1", run_id: "R1" }],
    ["a2ui.ui_resource_result", { id: "tc0", result: SUBMISSION }],
    ["a2ui.message.start", { id: "m1", role: "assistant" }],
    ["a2ui.message.delta", { id: "m1", delta: "Got it." }],
    ["a2ui.message.end", { id: "m1" }],
    ["a2ui.ui_resource", { id: "tc1", kind: "widget", ...PAYLOAD }],
    ["a2ui.state", { snapshot: { status: "awaiting_input", done: false } }],
    ["a2ui.run.end", { thread_id: "T1", run_id: "R1" }],
  ],
  "mcp-ui": [
    ["notifications/run/started", { threadId: "T1", runId: "R1" }],
    ["notifications/tools/result", { id: "tc0", content: [{ type: "text", text: JSON.stringify(SUBMISSION) }] }],
    ["notifications/message/started", { id: "m1", role: "assistant" }],
    ["notifications/message/delta", { id: "m1", contents: [{ type: "text", text: "Got it." }] }],
    ["notifications/message/completed", { id: "m1" }],
    [
      "notifications/resources/updated",
      {
        uri: "ui://widget/flow_transfer-funds/tc1",
        name: "flow_transfer-funds",
        mimeType: "application/vnd.aigenos.flow-spec+json",
        text: JSON.stringify({
          ...PAYLOAD,
          interaction: {
            tool_call_id: "tc1",
            resume: { method: "POST", url: "/chat/T1/resume", body_is_submission: true, thread_id: "T1" },
          },
        }),
      },
    ],
    ["notifications/state/snapshot", { state: { status: "awaiting_input", done: false } }],
    ["notifications/run/finished", { threadId: "T1", runId: "R1" }],
  ],
};

let failures = 0;
function check(label, ok, detail = "") {
  console.log(`${ok ? "  ok  " : "  FAIL"}  ${label}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures += 1;
}

const results = {};

for (const protocol of PROTOCOLS) {
  const seen = {
    threadId: null,
    text: "",
    widgets: [],
    payloads: [],
    results: [],
    state: null,
    finished: false,
    errors: [],
  };
  const sink = {
    runStarted: (tid) => { seen.threadId = tid; },
    textDelta: (_id, d) => { seen.text += d; },
    widgetStart: (tcid) => seen.widgets.push(tcid),
    widgetArgs: (tcid, delta) => { try { seen.payloads.push([tcid, JSON.parse(delta)]); } catch { /* partial */ } },
    widgetPayload: (tcid, p) => seen.payloads.push([tcid, p]),
    widgetResult: (tcid, sub) => seen.results.push([tcid, sub]),
    state: (s) => { seen.state = s; },
    runFinished: () => { seen.finished = true; },
    runError: (m) => seen.errors.push(m),
  };

  console.log(`\n[${protocol}]`);
  const names = eventTypesFor(protocol);
  for (const [event, data] of FIXTURES[protocol]) {
    check(`${event} is an advertised ${protocol} event`, names.includes(event));
    handleProtocolFrame(protocol, event, data, sink);
  }

  check("thread id parsed", seen.threadId === "T1", `got ${seen.threadId}`);
  check("assistant text parsed", seen.text === "Got it.", JSON.stringify(seen.text));
  check("exactly one widget skeleton", seen.widgets.length === 1, JSON.stringify(seen.widgets));
  check("widget payload delivered", seen.payloads.length === 1);
  const [, payload] = seen.payloads[0] ?? [null, {}];
  check("widget_id survives", payload.widget_id === "flow_transfer-funds", String(payload.widget_id));
  check("spec survives", JSON.stringify(payload.spec) === JSON.stringify(SPEC));
  check("submit_schema survives", !!payload.submit_schema);
  check(
    "the input's skill prop survives (inline validation needs it)",
    payload?.spec?.children?.[0]?.children?.[0]?.props?.skill === "transfer-funds",
  );
  check("prior widget resolved from the stream", seen.results.length === 1);
  check(
    "submission decoded to an object",
    JSON.stringify(seen.results[0]?.[1]) === JSON.stringify(SUBMISSION),
    JSON.stringify(seen.results[0]?.[1]),
  );
  check("run state reported", seen.state?.status === "awaiting_input", JSON.stringify(seen.state));
  check("run finished", seen.finished);
  check("no errors", seen.errors.length === 0, seen.errors.join(", "));

  results[protocol] = {
    text: seen.text,
    widget: payload.widget_id,
    spec: JSON.stringify(payload.spec),
    submission: JSON.stringify(seen.results[0]?.[1]),
    status: seen.state?.status,
  };
}

// The whole point: all three protocols converge on identical parsed output.
const canonical = JSON.stringify(results["ag-ui"]);
for (const protocol of PROTOCOLS) {
  check(
    `${protocol} parses to the same result as ag-ui`,
    JSON.stringify(results[protocol]) === canonical,
    JSON.stringify(results[protocol]),
  );
}

console.log(`\n${failures === 0 ? "PASS" : `FAIL (${failures})`} — protocols verified: ${PROTOCOLS.join(", ")}`);
process.exit(failures === 0 ? 0 : 1);
