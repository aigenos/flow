/**
 * Public surface for embedding aigenos/flow into your existing React app.
 *
 * Three integration tiers:
 *
 * 1) Drop-in chat shell — point it at your own chat router:
 *      import { Chat } from "@aigenos/flow";
 *      <Chat endpoint="/chat" />
 *
 * 2) Headless hook + your own UI (recommended for existing chats):
 *      import { useFlowChat, Widget } from "@aigenos/flow";
 *
 * 3) Just the renderer (you already have a chat + transport):
 *      import { SpecRenderer } from "@aigenos/flow";
 *
 * Module format: ESM. Importing from `@aigenos/flow` inside a bundler pulls the
 * stylesheet in for you; in plain Node (SSR, unit tests) the `node` export
 * condition serves a CSS-free build, so load `@aigenos/flow/style.css` yourself
 * wherever your app collects CSS.
 */

export { useFlowChat } from "./hooks/useFlowChat";
export type { UseFlowChatOptions, UseFlowChatReturn } from "./hooks/useFlowChat";

/** @deprecated Renamed to `useFlowChat` in 0.3.1; this alias is removed in 0.4. */
export { useFlowChat as useChatform } from "./hooks/useFlowChat";
/** @deprecated Renamed to `UseFlowChatOptions` / `UseFlowChatReturn` in 0.3.1. */
export type {
  UseFlowChatOptions as UseChatformOptions,
  UseFlowChatReturn as UseChatformReturn,
} from "./hooks/useFlowChat";

export { SpecRenderer } from "./renderer/SpecRenderer";
export { REGISTRY as PRIMITIVES } from "./renderer/primitives";

// Opt-in renderer configuration (live field validation). Without it the
// primitives make no network calls of their own.
export { FlowConfigProvider, useFlowRendererConfig } from "./renderer/FlowConfig";
export type { FlowRendererConfig, FieldValidation } from "./renderer/FlowConfig";

export { Chat } from "./chat/Chat";

// Voice input: a hook over the browser's speech recognition, and the button
// <Chat> uses. Dictation only — text lands in the box; nothing is sent.
export { useSpeechInput } from "./hooks/useSpeechInput";
export type { UseSpeechInputOptions, UseSpeechInputReturn } from "./hooks/useSpeechInput";
export { MicButton } from "./chat/MicButton";
// A hands-free conversation: listen, send, speak the answer, listen again.
export { useVoiceConversation, isVoiceAnswerable } from "./hooks/useVoiceConversation";
export type {
  UseVoiceConversationOptions,
  UseVoiceConversationReturn,
  VoicePhase,
} from "./hooks/useVoiceConversation";
export { VoicePanel, VoiceToggle } from "./chat/VoiceConversation";
export type { ChatProps } from "./chat/Chat";

export type {
  AgUiEvent,
  SpecNode,
  WidgetPayload,
  ChatMessage,
} from "./agui/types";

// Convenience alias — semantically "render one widget given its payload".
export { SpecRenderer as Widget } from "./renderer/SpecRenderer";
