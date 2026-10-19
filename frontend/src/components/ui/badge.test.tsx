import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge, type Tone } from "./badge";

describe("Badge", () => {
  const tones: Tone[] = ["neutral", "info", "success", "warning", "danger"];

  it.each(tones)("shows its text and an icon hidden from assistive tech (%s)", (tone) => {
    const { container } = render(<Badge tone={tone}>Status text</Badge>);
    expect(screen.getByText("Status text")).toBeInTheDocument();
    const icon = container.querySelector("svg");
    expect(icon).not.toBeNull();
    expect(icon).toHaveAttribute("aria-hidden", "true");
  });

  it("uses a different icon per tone so color is not the only signal", () => {
    const shapes = tones.map((tone) => {
      const { container, unmount } = render(<Badge tone={tone}>x</Badge>);
      const html = container.querySelector("svg")?.innerHTML ?? "";
      unmount();
      return html;
    });
    expect(new Set(shapes).size).toBe(tones.length);
  });
});
