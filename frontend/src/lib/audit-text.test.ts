import { describe, expect, it } from "vitest";

import { actionLabel, detailsSummary } from "./audit-text";

describe("audit text", () => {
  it("names known actions in words and leaves unknown ones visible", () => {
    expect(actionLabel("approval.approved")).toBe("Change approved");
    expect(actionLabel("something.new")).toBe("something.new");
  });

  it("summarises details without dumping them", () => {
    const text = detailsSummary({ result: "fixed", diff_sha256: "abcdef0123456789", secret: "x" });
    expect(text).toBe("result: fixed · change abcdef01");
    expect(text).not.toContain("secret");
  });
});
