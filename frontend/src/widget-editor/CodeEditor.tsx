import { lazy, Suspense } from "react";

const Editor = lazy(() => import("@monaco-editor/react"));

type Props = {
  value: string;
  onChange: (text: string) => void;
  language?: "json" | "yaml";
};

export function CodeEditor({ value, onChange, language = "json" }: Props) {
  return (
    <div className="h-full rounded-xl border border-border-subtle bg-bg-surface overflow-hidden">
      <Suspense fallback={<div className="h-full bg-bg-muted animate-pulse" />}>
        <Editor
          height="100%"
          language={language}
          value={value}
          onChange={(v) => onChange(v ?? "")}
          theme="vs-dark"
          options={{
            fontSize: 13,
            fontFamily: "JetBrains Mono, ui-monospace, monospace",
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            tabSize: 2,
            wordWrap: "on",
            padding: { top: 12, bottom: 12 },
            renderLineHighlight: "gutter",
          }}
        />
      </Suspense>
    </div>
  );
}
