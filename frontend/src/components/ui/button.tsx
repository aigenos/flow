import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/cn";

/**
 * shadcn-pattern Button themed to the aigenos token system.
 * `variant="default"` is the brand gradient CTA; `outline`/`ghost` for chrome.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-150 focus-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 active:scale-[0.98]",
  {
    variants: {
      variant: {
        default:
          "bg-brand text-brand-fg shadow-glow hover:bg-brand-hover",
        secondary:
          "bg-bg-muted text-text-primary border border-border-subtle hover:bg-bg-elevated hover:border-border",
        outline:
          "border border-border bg-transparent text-text-secondary hover:text-text-primary hover:bg-bg-muted/60",
        ghost:
          "text-text-secondary hover:text-text-primary hover:bg-bg-muted/60",
        destructive:
          "bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/30 hover:bg-rose-500/20",
        link: "text-brand underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 [&_svg]:size-4",
        sm: "h-7 rounded-md px-2.5 text-xs [&_svg]:size-3",
        lg: "h-10 rounded-xl px-6 [&_svg]:size-4",
        icon: "h-9 w-9 [&_svg]:size-4",
        "icon-sm": "h-7 w-7 rounded-md [&_svg]:size-3.5",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
