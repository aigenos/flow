/**
 * Per-node style system for widget specs.
 *
 * Any spec node may carry `props.style` — a small, author-friendly dictionary
 * (short keys, JSON-editable) that resolves to inline CSS on the primitive's
 * root element. Absent keys leave the theme defaults untouched, so existing
 * widgets render exactly as before.
 *
 *   { "type": "text",
 *     "props": { "value": "Total due",
 *                "style": { "font": "serif", "size": 18, "italic": true,
 *                           "color": "rgb(var(--brand))" } } }
 *
 * Theme-token colors (`rgb(var(--brand))` etc.) are legal values — CSS
 * variables resolve at the element, so styled widgets still follow light/dark
 * theme switches.
 */
import type { CSSProperties } from "react";

export type WidgetStyle = {
  /* typography */
  font?: "sans" | "serif" | "mono" | (string & {});
  size?: number | string;
  weight?: number | string;
  italic?: boolean;
  underline?: boolean;
  strike?: boolean;
  spacing?: number | string; // letter-spacing
  leading?: number | string; // line-height
  align?: "left" | "center" | "right" | "justify";
  transform?: "none" | "uppercase" | "lowercase" | "capitalize";
  color?: string;
  /* appearance */
  bg?: string;
  borderColor?: string;
  borderWidth?: number | string;
  radius?: number | string;
  shadow?: "none" | "sm" | "md" | "lg" | "glow";
  opacity?: number; // 0–1 or 0–100
  /* layout */
  pad?: number | string;
  gap?: number | string;
  width?: number | string;
  maxWidth?: number | string;
};

export const FONT_STACKS: Record<string, string> = {
  sans: "Inter, system-ui, ui-sans-serif, sans-serif",
  serif: "Georgia, 'Iowan Old Style', 'Times New Roman', serif",
  mono: "'JetBrains Mono', ui-monospace, SFMono-Regular, monospace",
};

const SHADOWS: Record<string, string> = {
  none: "none",
  sm: "0 1px 2px rgb(0 0 0 / 0.25)",
  md: "0 4px 14px -2px rgb(0 0 0 / 0.35)",
  lg: "0 14px 40px -10px rgb(0 0 0 / 0.5)",
  glow: "0 0 0 1px rgb(var(--brand) / 0.35), 0 8px 30px -6px rgb(var(--brand) / 0.45)",
};

const px = (v: number | string | undefined): string | undefined =>
  v === undefined || v === "" ? undefined : typeof v === "number" ? `${v}px` : v;

/** Resolve `props.style` into inline CSS. Returns undefined when unstyled. */
export function styleOf(props?: Record<string, unknown>): CSSProperties | undefined {
  const s = (props?.style ?? undefined) as WidgetStyle | undefined;
  if (!s || typeof s !== "object") return undefined;

  const css: CSSProperties = {};

  if (s.font) css.fontFamily = FONT_STACKS[s.font] ?? s.font;
  if (s.size !== undefined) css.fontSize = px(s.size);
  if (s.weight !== undefined) css.fontWeight = s.weight as CSSProperties["fontWeight"];
  if (s.italic) css.fontStyle = "italic";
  const deco = [s.underline && "underline", s.strike && "line-through"].filter(Boolean);
  if (deco.length) css.textDecoration = deco.join(" ");
  if (s.spacing !== undefined) css.letterSpacing = px(s.spacing);
  if (s.leading !== undefined)
    css.lineHeight = typeof s.leading === "number" && s.leading <= 4 ? s.leading : px(s.leading);
  if (s.align) css.textAlign = s.align;
  if (s.transform && s.transform !== "none") css.textTransform = s.transform;
  if (s.color) css.color = s.color;

  if (s.bg) css.background = s.bg;
  if (s.borderColor) css.borderColor = s.borderColor;
  if (s.borderWidth !== undefined) {
    css.borderWidth = px(s.borderWidth);
    css.borderStyle = "solid";
  }
  if (s.radius !== undefined) css.borderRadius = px(s.radius);
  if (s.shadow) css.boxShadow = SHADOWS[s.shadow] ?? s.shadow;
  if (s.opacity !== undefined) css.opacity = s.opacity > 1 ? s.opacity / 100 : s.opacity;

  if (s.pad !== undefined) css.padding = px(s.pad);
  if (s.gap !== undefined) css.gap = px(s.gap);
  if (s.width !== undefined) css.width = px(s.width);
  if (s.maxWidth !== undefined) css.maxWidth = px(s.maxWidth);

  return Object.keys(css).length ? css : undefined;
}

/** Text-cascade subset — applied directly to <input>/<select>/<table> elements
 *  whose utility classes would otherwise beat inherited typography. */
export function textStyleOf(props?: Record<string, unknown>): CSSProperties | undefined {
  const full = styleOf(props);
  if (!full) return undefined;
  const pick: (keyof CSSProperties)[] = [
    "fontFamily", "fontSize", "fontWeight", "fontStyle", "letterSpacing",
    "textTransform", "color",
  ];
  const out: CSSProperties = {};
  for (const k of pick) if (full[k] !== undefined) (out as any)[k] = full[k];
  return Object.keys(out).length ? out : undefined;
}

/**
 * Root attributes for a primitive: resolved inline style, plus a
 * `data-aig-id` marker when the node carries an editor id (visual builder
 * click-to-select). In production specs `_id` is absent, so nothing leaks.
 */
export function rootAttrs(node: { props?: Record<string, unknown> } & Record<string, unknown>): {
  style?: CSSProperties;
  "data-aig-id"?: string;
} {
  const out: { style?: CSSProperties; "data-aig-id"?: string } = {};
  const style = styleOf(node.props);
  if (style) out.style = style;
  if (typeof node._id === "string") out["data-aig-id"] = node._id;
  return out;
}
