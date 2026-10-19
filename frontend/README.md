# @aigenos/flow

> **React renderer for [aigenos/flow](https://github.com/aigenos/flow) —
> typed widgets inside your AI chat.**
>
> Your agent serves a validated form, the user fills it, the result renders as a
> card, grid or timeline. Nothing to install at runtime beyond React.

Pairs with the [`aigenos-flow`](https://pypi.org/project/aigenos-flow/) Python
package, whose FastAPI router streams widget events over **AG-UI**, **A2UI** or
**MCP-UI**. It reads the event shapes that router emits (the exported `AgUiEvent`
type); another backend works if it emits the same shapes.

## Install

```bash
npm install @aigenos/flow
```

## Three integration tiers

**1 · Drop-in chat shell** — the whole UI in one component:

```tsx
import { Chat } from "@aigenos/flow";
import "@aigenos/flow/style.css";

<Chat endpoint="/chat" />
```

`<Chat>` also takes `protocol`, `skill`, `threadId`, `placeholder`, `emptyState`,
`className`, `validateEndpoint`, `onError` and `voice`.

Everything here uses React state and context, so in the Next.js App Router
import it from a file that starts with `"use client"`.

### Voice

`<Chat>` has two voice controls. Pass `voice={false}` to hide both.

- **The microphone dictates.** Speech becomes text in the message box; the user
  reads it and presses Send.
- **Talk is a hands-free conversation.** It listens, sends what it heard,
  speaks the answer, and listens again. Spoken words travel the same path as
  typed ones, so the engine's rules apply unchanged.

What Talk will **not** do is answer a step that commits or reveals something: a
yes/no confirmation, a secret such as a one-time code, or a choice from a grid.
For those it asks out loud, stops listening, and waits for the answer on screen.
A recognizer that mishears "no" as "now" should not be able to move money.

Both use the browser's own speech services (Chrome, Edge, Safari), so audio is
handled by the browser, not by this package; where the browser has none, the
controls are not rendered. Building your own UI? The same pieces are exported:

```tsx
import { MicButton, VoicePanel, VoiceToggle, useVoiceConversation } from "@aigenos/flow";

const voice = useVoiceConversation({ messages, status, send });

<VoicePanel voice={voice} />
<MicButton value={draft} onChange={setDraft} />
<VoiceToggle voice={voice} />
```

**2 · Headless hook + your own UI** (recommended for an existing chat):

```tsx
import { useFlowChat, Widget } from "@aigenos/flow";

function MyChat() {
  const { messages, send, submitWidget } = useFlowChat({
    endpoint: "/chat",          // your aigenos-flow backend
    protocol: "ag-ui",          // "ag-ui" | "a2ui" | "mcp-ui"
  });

  return messages.map((m) =>
    m.kind === "widget"
      ? <Widget key={m.id} spec={m.payload.spec}
                onSubmit={(v) => submitWidget(m.tool_call_id, v)} />
      : <p key={m.id}>{m.content}</p>
  );
}
```

The hook also returns `threadId`, `status` (`"idle" | "live" |
"waiting_for_widget" | "error"`), `error` (a string saying why the last turn
failed, with the server's message when there is one; `null` when nothing
failed), `reset()` and `pushUserMessage()`.

**3 · Just the renderer** — you already have a chat and a transport:

```tsx
import { SpecRenderer } from "@aigenos/flow";

<SpecRenderer spec={widgetSpec} onSubmit={handlePayload} />
```

(`Widget` is an alias of `SpecRenderer`, for when you are rendering exactly one.)

## 16 primitives

`card · text · row · column · divider · alert · badge · button · input ·
select · form · image · loading` plus the three data-bound ones:
`autosuggest · datatable · timeline`.

Compose them yourself, or let the backend generate a widget from each field's
type: an `enum` becomes a select, a bounded `number` becomes a number input
carrying its min/max (an `integer` also gets `step` 1), a `pick:` step becomes a
data grid.

Every primitive accepts the shared style system (`props.style`), so widgets
designed in the Studio render the same in your app.

## Inline field validation is opt-in

The renderer never guesses a URL in someone else's app. To check a value against
its `FieldSpec` when the user leaves the field, point at your flow router's endpoint —
per component, or once for a subtree:

```tsx
import { FlowConfigProvider } from "@aigenos/flow";

<FlowConfigProvider value={{ validateEndpoint: "/api/flow/validate-field", skill: "transfer-funds" }}>
  <Widget spec={spec} onSubmit={submit} />
</FlowConfigProvider>
```

Leave it out and no request is ever made.

## Dependencies

No runtime dependencies to install: React and react-dom are peer dependencies.
Three small libraries (`clsx`, `lucide-react`, `tailwind-merge` — MIT/ISC) are
bundled into the published files; see `THIRD-PARTY-NOTICES.md`.

The package ships an ESM build for bundlers, a CommonJS build, and a CSS-free
build served through the `node` export condition so server-side rendering does
not trip over the stylesheet import.

## Renamed since the pre-release builds

`useChatform` was renamed **`useFlowChat`** (with `UseChatformOptions` →
`UseFlowChatOptions`). The old names still work as deprecated aliases and are
removed in 0.4.

## Links

- Docs, Studio and the 10-minute tutorial: https://github.com/aigenos/flow
- Site: https://aigenos.io

## License

Apache-2.0
