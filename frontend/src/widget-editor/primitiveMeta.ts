/**
 * Author-time metadata for each primitive: which props it accepts, types,
 * defaults, options. The visual builder uses this to render the inspector
 * and seed new nodes with sensible defaults.
 */

export type FieldType = "string" | "number" | "boolean" | "select" | "json" | "textarea";

export type FieldMeta = {
  name: string;
  label: string;
  type: FieldType;
  default?: unknown;
  options?: Array<{ value: string; label: string }>;
  help?: string;
};

export type PrimitiveCategory = "layout" | "content" | "inputs" | "data";

export type PrimitiveMeta = {
  type: string;
  label: string;
  icon?: string;
  container: boolean;
  category: PrimitiveCategory;
  /** One-liner shown as a tooltip in the palette. */
  hint?: string;
  fields: FieldMeta[];
};

export const CATEGORY_LABELS: Record<PrimitiveCategory, string> = {
  layout: "Layout",
  content: "Content",
  inputs: "Inputs",
  data: "Data",
};

const tones = [
  { value: "default", label: "Default" },
  { value: "info", label: "Info" },
  { value: "warning", label: "Warning" },
  { value: "danger", label: "Danger" },
  { value: "success", label: "Success" },
];

export const PRIMITIVES: PrimitiveMeta[] = [
  {
    type: "card",
    label: "Card",
    container: true,
    category: "layout",
    hint: "Framed surface with title/subtitle — the usual widget root",
    fields: [
      { name: "title", label: "Title", type: "string" },
      { name: "subtitle", label: "Subtitle", type: "string" },
      { name: "tone", label: "Tone", type: "select", options: tones, default: "default" },
    ],
  },
  {
    type: "text",
    label: "Text",
    container: false,
    category: "content",
    hint: "Paragraph text — full typography controls in the inspector",
    fields: [
      { name: "value", label: "Value", type: "textarea", help: "Supports {{bindings}}" },
      { name: "muted", label: "Muted", type: "boolean", default: false },
      {
        name: "weight",
        label: "Weight",
        type: "select",
        options: [{ value: "normal", label: "Normal" }, { value: "bold", label: "Bold" }],
        default: "normal",
      },
    ],
  },
  {
    type: "row",
    label: "Row",
    container: true,
    category: "layout",
    hint: "Horizontal group — stacks on narrow embeds",
    fields: [
      {
        name: "justify",
        label: "Justify",
        type: "select",
        options: [
          { value: "start", label: "Start" }, { value: "center", label: "Center" },
          { value: "end", label: "End" }, { value: "between", label: "Between" },
        ],
        default: "start",
      },
      {
        name: "gap", label: "Gap", type: "select",
        options: [{ value: "sm", label: "Small" }, { value: "md", label: "Medium" }, { value: "lg", label: "Large" }],
        default: "md",
      },
    ],
  },
  {
    type: "column",
    label: "Column",
    container: true,
    category: "layout",
    hint: "Vertical group",
    fields: [
      {
        name: "gap", label: "Gap", type: "select",
        options: [{ value: "sm", label: "Small" }, { value: "md", label: "Medium" }, { value: "lg", label: "Large" }],
        default: "md",
      },
    ],
  },
  { type: "divider", label: "Divider", container: false, category: "layout", hint: "Hairline separator", fields: [] },
  {
    type: "alert",
    label: "Alert",
    container: false,
    category: "content",
    hint: "Status banner (info / warning / danger / success)",
    fields: [
      { name: "tone", label: "Tone", type: "select", options: tones, default: "info" },
      { name: "message", label: "Message", type: "textarea" },
    ],
  },
  {
    type: "badge",
    label: "Badge",
    container: false,
    category: "content",
    hint: "Small status pill",
    fields: [{ name: "label", label: "Label", type: "string" }],
  },
  {
    type: "button",
    label: "Button",
    container: false,
    category: "inputs",
    hint: "Action button — submit posts its payload to the flow",
    fields: [
      { name: "label", label: "Label", type: "string", default: "Submit" },
      {
        name: "variant", label: "Variant", type: "select",
        options: [
          { value: "primary", label: "Primary" }, { value: "secondary", label: "Secondary" },
          { value: "ghost", label: "Ghost" }, { value: "danger", label: "Danger" },
        ],
        default: "primary",
      },
      {
        name: "action", label: "Action", type: "select",
        options: [{ value: "submit", label: "Submit" }, { value: "button", label: "Plain button" }],
        default: "submit",
      },
      { name: "payload", label: "Submit payload (JSON)", type: "json", default: {}, help: "Posted to backend on click" },
    ],
  },
  {
    type: "input",
    label: "Input",
    container: false,
    category: "inputs",
    hint: "Text field — name becomes the submitted form key",
    fields: [
      { name: "name", label: "Field name", type: "string", default: "value" },
      { name: "label", label: "Label", type: "string" },
      { name: "placeholder", label: "Placeholder", type: "string" },
      {
        name: "type", label: "Input type", type: "select",
        options: [
          { value: "text", label: "Text" }, { value: "email", label: "Email" },
          { value: "number", label: "Number" }, { value: "password", label: "Password" },
          { value: "date", label: "Date" }, { value: "tel", label: "Phone" },
        ],
        default: "text",
      },
      { name: "required", label: "Required", type: "boolean", default: false },
    ],
  },
  {
    type: "select",
    label: "Select",
    container: false,
    category: "inputs",
    hint: "Dropdown choice",
    fields: [
      { name: "name", label: "Field name", type: "string", default: "value" },
      { name: "label", label: "Label", type: "string" },
      { name: "options", label: "Options (JSON array)", type: "json", default: [{ value: "a", label: "A" }] },
      { name: "required", label: "Required", type: "boolean", default: false },
    ],
  },
  {
    type: "form",
    label: "Form",
    container: true,
    category: "layout",
    hint: "Collects child inputs and submits them as one payload",
    fields: [{ name: "submitLabel", label: "Submit button label", type: "string", default: "Submit" }],
  },
  {
    type: "image",
    label: "Image",
    container: false,
    category: "content",
    hint: "Picture by URL",
    fields: [
      { name: "src", label: "Image URL", type: "string" },
      { name: "alt", label: "Alt text", type: "string" },
    ],
  },
  {
    type: "autosuggest",
    label: "Autosuggest",
    container: false,
    category: "data",
    hint: "Type-ahead over rows — the pick step's search rendering",
    fields: [
      { name: "label", label: "Label", type: "string" },
      { name: "placeholder", label: "Placeholder", type: "string", default: "Type to search…" },
      { name: "display", label: "Display key", type: "string", default: "label", help: "Row key shown in the list" },
      {
        name: "options", label: "Rows (JSON array)", type: "json",
        default: [{ label: "Acme Corp", id: "a1" }, { label: "Globex", id: "g2" }],
      },
      {
        name: "fields", label: "Save mapping (JSON)", type: "json", default: {},
        help: "formField → row key, e.g. {\"client_id\": \"id\"}",
      },
    ],
  },
  {
    type: "datatable",
    label: "Data table",
    container: false,
    category: "data",
    hint: "Selectable rows — the pick step's default rendering",
    fields: [
      {
        name: "columns", label: "Columns (JSON)", type: "json",
        default: [{ key: "name", label: "Name" }, { key: "amount", label: "Amount" }],
      },
      {
        name: "rows", label: "Rows (JSON)", type: "json",
        default: [{ name: "Policy A-1042", amount: "$120.00" }, { name: "Policy B-2210", amount: "$88.50" }],
      },
      {
        name: "fields", label: "Save mapping (JSON)", type: "json", default: {},
        help: "formField → row key",
      },
    ],
  },
  {
    type: "timeline",
    label: "Timeline",
    container: false,
    category: "data",
    hint: "Dated events — autowidget picks this for dated lists",
    fields: [
      {
        name: "events", label: "Events (JSON)", type: "json",
        default: [
          { time: "09:12", title: "Claim created", description: "Filed online" },
          { time: "14:40", title: "Approved", description: "Auto-adjudicated" },
        ],
      },
    ],
  },
];

export const PRIMITIVE_BY_TYPE: Record<string, PrimitiveMeta> = Object.fromEntries(
  PRIMITIVES.map((p) => [p.type, p]),
);

export function defaultProps(type: string): Record<string, unknown> {
  const meta = PRIMITIVE_BY_TYPE[type];
  if (!meta) return {};
  const out: Record<string, unknown> = {};
  for (const f of meta.fields) {
    if (f.default !== undefined) out[f.name] = f.default;
  }
  return out;
}
