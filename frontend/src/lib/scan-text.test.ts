import { describe, expect, it } from "vitest";

import {
  describe as describeObservation,
  reasonsText,
  errorText,
  formatBytes,
  formatDuration,
  stopText,
} from "./scan-text";
import type { Observation } from "./types";

const obs = (kind: string, data: Record<string, unknown>): Observation => ({
  id: "1",
  kind,
  severity: "info",
  page_url: null,
  data,
  evidence_id: null,
  created_at: "2026-01-01T00:00:00Z",
});

describe("scan text", () => {
  it("turns every known failure code into a sentence, never the raw code", () => {
    for (const code of [
      "site_unreachable",
      "timeout",
      "browser_unavailable",
      "blocked_address",
      "worker_lost",
    ]) {
      const text = errorText(code);
      expect(text).toBeTruthy();
      expect(text).not.toContain("_");
    }
    expect(errorText("something_new")).toBe("The scan failed.");
    expect(errorText(null)).toBeNull();
  });

  it("explains why a scan stopped early", () => {
    expect(stopText("page_limit")).toMatch(/page limit/i);
    expect(stopText("unknown")).toBeNull();
  });

  it("formats durations and sizes for people", () => {
    expect(formatDuration(450)).toBe("450 ms");
    expect(formatDuration(5200)).toBe("5 s");
    expect(formatDuration(125000)).toBe("2 min 5 s");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatBytes(900)).toBe("900 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });

  it("describes observations in plain language", () => {
    expect(describeObservation(obs("page", { title: "Home", status: 200, load_ms: 120 }))).toBe(
      "“Home”, HTTP 200, loaded in 120 ms",
    );
    expect(describeObservation(obs("page", { status: null, error: "net::ERR_X" }))).toMatch(
      /Could not load/,
    );
    expect(describeObservation(obs("a11y_issue", { check: "images_missing_alt", count: 1 }))).toBe(
      "1 image without alternative text",
    );
    expect(describeObservation(obs("a11y_issue", { check: "images_missing_alt", count: 3 }))).toBe(
      "3 images without alternative text",
    );
    expect(describeObservation(obs("blocked_request", { reason: "non_get_blocked" }))).toMatch(
      /never change/,
    );
    expect(
      describeObservation(obs("blocked_request", { reason: "proxy_blocked_address" })),
    ).toMatch(/private/);
    expect(describeObservation(obs("console_error", { message: "boom" }))).toBe(
      "Console error: boom",
    );
  });

  it("does not crash on observation kinds or reasons it has never seen", () => {
    expect(describeObservation(obs("future_kind", {}))).toBe("Note.");
    expect(describeObservation(obs("blocked_request", { reason: "new_reason" }))).toMatch(
      /safety rules/,
    );
    expect(describeObservation(obs("a11y_issue", { check: "new_check" }))).toMatch(
      /accessibility/i,
    );
  });

  it("explains API check results without ever including response content", () => {
    expect(
      reasonsText([
        { code: "status_mismatch", expected: 200, actual: 500 },
        { code: "too_slow", max_ms: 100, actual_ms: 900 },
        { code: "schema_mismatch", violations: [{ path: "$.id", rule: "type" }] },
        { code: "auth_rejected", actual: 401 },
      ]),
    ).toBe(
      "expected HTTP 200, got 500; took 900 ms, limit 100 ms; the response shape does not match ($.id: type); the API rejected the credentials you provided (HTTP 401)",
    );
    expect(reasonsText([{ code: "something_new" }])).toBe("something new");
    expect(
      describeObservation(
        obs("api_check", { name: "Products", status: "passed", http_status: 200, latency_ms: 30 }),
      ),
    ).toBe("API check “Products” passed (HTTP 200, 30 ms)");
    expect(
      describeObservation(
        obs("api_check", {
          name: "Products",
          status: "failed",
          reasons: [{ code: "invalid_json" }],
        }),
      ),
    ).toBe("API check “Products” failed: the response was not valid JSON");
  });
});
