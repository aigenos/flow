import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { cn } from "../lib/cn";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  className?: string;
  viewportClassName?: string;
};

export function AppDialog({ open, onOpenChange, children, className, viewportClassName }: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop" />
        <Dialog.Content className={cn("dialog-viewport", viewportClassName)}>
          <div className={cn("dialog-popup theme-transition", className)}>
            {children}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const AppDialogTitle = Dialog.Title;
export const AppDialogDescription = Dialog.Description;
export const AppDialogClose = Dialog.Close;
