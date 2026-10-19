"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "./button";

/** Copies text and says so. The label names what is being copied, for screen readers. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false); // clipboard blocked: the text is still selectable on screen
    }
  }
  return (
    <Button variant="secondary" size="sm" onClick={copy} aria-label={`Copy ${label}`}>
      {copied ? (
        <Check className="size-4" aria-hidden="true" />
      ) : (
        <Copy className="size-4" aria-hidden="true" />
      )}
      <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
    </Button>
  );
}
