/**
 * Renderer configuration for embedded use.
 *
 * The widget primitives are meant to be dropped into SOMEONE ELSE'S app, so
 * nothing in them may assume a same-origin aigenos-flow server. Anything that
 * needs a network call reads its endpoint from this context, and every such
 * feature is OFF unless the host opts in:
 *
 *   import { FlowConfigProvider, Widget } from "@aigenos/flow";
 *
 *   <FlowConfigProvider value={{ validateEndpoint: "/api/flow/validate-field", skill: "transfer-funds" }}>
 *     <Widget spec={spec} onSubmit={submit} />
 *   </FlowConfigProvider>
 *
 * Without a provider (or with `validateEndpoint: false`) the inputs do exactly
 * what a plain <input> does: no requests, no inline valid/invalid affordances.
 */
import { createContext, useContext, useMemo } from "react";
import type { ReactNode } from "react";

export type FlowRendererConfig = {
  /**
   * Absolute or same-origin URL of the aigenos-flow `validate-field` endpoint
   * (the flow router mounted at `<prefix>/validate-field`). When omitted or
   * `false`, live per-field validation is disabled and no request is made.
   */
  validateEndpoint?: string | false;
  /**
   * Skill whose FieldSpecs the endpoint should validate against. Required by
   * the server to resolve a field; a spec node may also carry its own `skill`
   * prop, which wins over this default.
   */
  skill?: string;
};

const EMPTY: FlowRendererConfig = Object.freeze({});

const FlowConfigContext = createContext<FlowRendererConfig>(EMPTY);

export function FlowConfigProvider({
  value,
  children,
}: {
  value: FlowRendererConfig;
  children: ReactNode;
}) {
  // Stable identity for the common inline-object case, so consumers don't have
  // to memoize the literal themselves.
  const memo = useMemo(
    () => ({ validateEndpoint: value.validateEndpoint, skill: value.skill }),
    [value.validateEndpoint, value.skill],
  );
  return <FlowConfigContext.Provider value={memo}>{children}</FlowConfigContext.Provider>;
}

export function useFlowRendererConfig(): FlowRendererConfig {
  return useContext(FlowConfigContext);
}

export type FieldValidation = { valid: boolean; error?: string; coerced?: unknown };

/**
 * POST one field value to an explicit endpoint. There is deliberately no
 * default URL: a renderer embedded in another app must never guess one.
 */
export async function validateFieldAt(
  endpoint: string,
  field: string,
  value: unknown,
  skill?: string,
): Promise<FieldValidation> {
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ field, value, skill }),
  });
  if (!res.ok) throw new Error(`validate-field ${res.status}`);
  return (await res.json()) as FieldValidation;
}
