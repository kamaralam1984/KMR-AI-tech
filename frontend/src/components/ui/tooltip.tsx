import { cloneElement, isValidElement, type ReactElement, useId } from "react";

/** Shows on hover and keyboard focus. The child must be a focusable element. */
export function Tooltip({
  content,
  children,
}: {
  content: string;
  children: ReactElement<{ "aria-describedby"?: string }>;
}) {
  const id = useId();
  const trigger = isValidElement(children)
    ? cloneElement(children, { "aria-describedby": id })
    : children;
  return (
    <span className="group relative inline-flex">
      {trigger}
      <span
        id={id}
        role="tooltip"
        className="pointer-events-none absolute top-full right-0 z-30 mt-2 w-56 rounded-md bg-navy-900 px-3 py-2 text-xs text-white opacity-0 shadow-pop transition-opacity duration-150 group-focus-within:opacity-100 group-hover:opacity-100"
      >
        {content}
      </span>
    </span>
  );
}
