import { cn } from "../../lib/cn";

/** Shimmering placeholder for loading states. */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-shimmer rounded-lg bg-[linear-gradient(110deg,rgb(var(--bg-muted))_35%,rgb(var(--bg-elevated))_50%,rgb(var(--bg-muted))_65%)] bg-[length:200%_100%]",
        className,
      )}
      {...props}
    />
  );
}

export { Skeleton };
