import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";

type Meta = Record<string, unknown> | null;
type Spec = Record<string, unknown>;
type Core = {
  checkFile: (spec: Spec, meta: Meta) => { status: string; message: string };
  planResize: (
    meta: Meta,
    spec: Spec,
  ) => { width: number; height: number; qualities: number[] } | null;
};

const fixture = JSON.parse(
  readFileSync(resolve(__dirname, "../../../backend/tests/fixtures/file_checks.json"), "utf8"),
) as { spec: Spec; cases: { name: string; meta: Meta; status: string }[] };

let core: Core;

beforeAll(async () => {
  // The widget file is plain browser JavaScript. Loading it defines KMRWidgetCore and, because
  // there is no <script data-install-key>, mounts nothing.
  await import(/* @vite-ignore */ resolve(__dirname, "../../public/widget/kmr-widget.js"));
  core = (globalThis as unknown as { KMRWidgetCore: Core }).KMRWidgetCore;
});

describe("widget file rules (shared with the server)", () => {
  it.each(fixture.cases)("$name", ({ meta, status }) => {
    expect(core.checkFile(fixture.spec, meta).status).toBe(status);
  });

  it("explains a rejection in words a visitor can act on", () => {
    const r = core.checkFile(fixture.spec, {
      mime: "image/jpeg",
      size_bytes: 300000,
      width: 300,
      height: 400,
    });
    expect(r.message).toMatch(/maximum is 100 KB/);
    expect(core.checkFile(fixture.spec, { mime: "image/png", size_bytes: 5000 }).message).toMatch(
      /JPEG/,
    );
  });
});

describe("resize planning", () => {
  const spec = { ...fixture.spec };
  it("scales an oversized photo down without changing its shape", () => {
    const plan = core.planResize(
      { mime: "image/jpeg", size_bytes: 900000, width: 800, height: 1000 },
      spec,
    );
    expect(plan).not.toBeNull();
    expect(plan!.height).toBeLessThanOrEqual(500);
    expect(plan!.width).toBeLessThanOrEqual(400);
    expect(Math.abs(plan!.width / plan!.height - 0.8)).toBeLessThan(0.01);
  });

  it("will not enlarge a small image, which would invent detail", () => {
    expect(
      core.planResize({ mime: "image/jpeg", size_bytes: 5000, width: 150, height: 150 }, spec),
    ).toBeNull();
  });

  it("offers nothing for a file that is not a convertible image", () => {
    expect(core.planResize({ mime: "application/pdf", size_bytes: 900000 }, spec)).toBeNull();
    expect(core.planResize({ mime: "image/jpeg", size_bytes: 900000 }, spec)).toBeNull();
  });

  it("keeps the image within the minimum size even after scaling", () => {
    const tight = { ...spec, min_width: 350, max_width: 400 };
    expect(
      core.planResize({ mime: "image/jpeg", size_bytes: 1, width: 1000, height: 1000 }, tight),
    ).not.toBeNull();
    const impossible = {
      ...spec,
      min_width: 390,
      max_width: 400,
      min_height: 490,
      max_height: 500,
    };
    expect(
      core.planResize({ mime: "image/jpeg", size_bytes: 1, width: 1000, height: 500 }, impossible),
    ).toBeNull();
  });
});

describe("widget file hygiene", () => {
  const source = readFileSync(resolve(__dirname, "../../public/widget/kmr-widget.js"), "utf8");
  it("never writes server text into the page as HTML", () => {
    expect(source).not.toMatch(/\.innerHTML\s*=/);
    expect(source).not.toMatch(/insertAdjacentHTML|document\.write|eval\(/);
  });
  it("keeps nothing in browser storage and sends no credentials", () => {
    expect(source).not.toMatch(/localStorage|sessionStorage|document\.cookie|indexedDB/);
    expect(source).toMatch(/credentials: "omit"/);
  });
  it("sends the consent token as a header and never stores it", () => {
    expect(source).toMatch(/"X-KMR-Consent": CONSENT/);
    expect(source).toMatch(/data-consent-token/);
    expect(source).not.toMatch(/localStorage|sessionStorage|document\.cookie/);
  });
  it("has no way to upload a file or submit a host form", () => {
    expect(source).not.toMatch(/FormData|\.submit\(|readAsDataURL|readAsArrayBuffer/);
  });
});
