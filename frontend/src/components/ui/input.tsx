import * as React from "react";
import { cn } from "../../lib/cn";

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      className={cn(
        "flex h-9 w-full rounded-lg border border-border bg-bg-muted/70 px-3 py-1 text-sm text-text-primary shadow-sm transition-all",
        "placeholder:text-text-muted",
        "focus:outline-none focus:border-brand/60 focus:ring-2 focus:ring-brand/25 focus:bg-bg-muted",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-[60px] w-full rounded-lg border border-border bg-bg-muted/70 px-3 py-2 text-sm text-text-primary shadow-sm transition-all",
        "placeholder:text-text-muted",
        "focus:outline-none focus:border-brand/60 focus:ring-2 focus:ring-brand/25 focus:bg-bg-muted",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export { Input, Textarea };
