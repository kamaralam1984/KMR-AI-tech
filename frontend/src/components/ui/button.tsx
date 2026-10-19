import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "gold";
type Size = "sm" | "md";

const variants: Record<Variant, string> = {
  primary: "bg-primary text-white hover:bg-primary-strong",
  secondary: "border border-line bg-surface text-ink hover:bg-page",
  ghost: "text-ink hover:bg-primary-soft",
  danger: "bg-danger-text text-white hover:bg-[#8f1c13]",
  gold: "gold-button font-semibold",
};

const sizes: Record<Size, string> = {
  // 40-44px tall on small screens (touch), compact on desktop.
  sm: "h-10 px-3 text-sm lg:h-8",
  md: "h-11 px-4 text-sm lg:h-10",
};

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  isLoading?: boolean;
};

export function Button({
  variant = "primary",
  size = "md",
  isLoading = false,
  disabled,
  className,
  children,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors duration-150",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {isLoading ? (
        <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" />
      ) : null}
      {children}
    </button>
  );
}
