import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * Catches render errors anywhere below it and shows a message instead of a
 * blank page. Without this, one component reading `undefined.length` unmounts
 * the entire app — exactly the "blank tab" failure mode we hit when the
 * backend returned an unexpected shape.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="mx-auto max-w-lg px-6 py-16 text-center">
        <h2 className="text-lg font-semibold text-text-primary">Something went wrong on this screen</h2>
        <p className="mt-2 text-sm text-text-secondary">
          The UI hit an error while rendering. This usually means the backend isn’t
          running on <code className="font-mono">:8000</code>, or it’s the wrong
          server. Start the library backend and reload:
        </p>
        <pre className="mt-3 rounded-lg border border-border-subtle bg-bg-muted px-4 py-3 text-left text-xs text-text-primary overflow-x-auto">
          uvicorn aigenos_flow.main:app --port 8000 --reload
        </pre>
        <p className="mt-3 text-xs text-text-muted font-mono break-words">{this.state.error.message}</p>
        <button
          onClick={() => this.setState({ error: null })}
          className="mt-4 inline-flex items-center rounded-lg bg-brand text-brand-fg px-4 py-2 text-sm font-medium hover:bg-brand-hover transition-colors focus-ring"
        >
          Try again
        </button>
      </div>
    );
  }
}
