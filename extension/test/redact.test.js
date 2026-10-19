import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const redact = require("../src/redact.js");
const { cases } = require("./redaction-cases.json");

const leaked = (c) => {
  const out = redact.redactText(c.text, null, 2000);
  return c.must_not_contain.filter((s) => out.includes(s));
};

describe("redaction, measured on the labeled set", () => {
  const caught = cases.filter((c) => c.expect === "caught");
  const known = cases.filter((c) => c.expect === "known_miss");
  const clean = cases.filter((c) => c.expect === "unchanged");

  it("catches every case labeled as covered (a regression gate)", () => {
    const misses = caught
      .filter((c) => leaked(c).length)
      .map((c) => `${c.category}: ${c.text}`);
    expect(misses).toEqual([]);
  });

  it("does not mangle ordinary text (false positives)", () => {
    const changed = clean
      .filter((c) => redact.redactText(c.text) !== c.text)
      .map((c) => c.text);
    expect(changed).toEqual([]);
  });

  it("reports the honest miss rate, including the documented limits", () => {
    const missedKnown = known.filter((c) => leaked(c).length);
    const labeled = caught.length + known.length;
    const missRate =
      (caught.filter((c) => leaked(c).length).length + missedKnown.length) /
      labeled;
    // The known-miss cases are expected to leak (names in sentences, spelled-out numbers...). If
    // one starts being caught, the label should be updated, so this fails to prompt the change.
    const nowCaught = known.filter((c) => !leaked(c).length).map((c) => c.text);
    console.log(
      `redaction: ${labeled} labeled cases, miss rate ${(missRate * 100).toFixed(1)}%, ` +
        `${missedKnown.length}/${known.length} known-limit cases still leak`,
    );
    expect(nowCaught).toEqual([]);
    expect(missRate).toBeGreaterThan(0); // never claim zero
    expect(missRate).toBeLessThan(0.1);
  });
});

// Key-shaped strings are kept out of the repository (GitHub push protection rightly refuses them),
// so the fixtures hold markers that are expanded here.
const MARKERS = {
  "@@SLACK_TOKEN@@": "xo" + "xb-123456789012-abcdefghijklmnop",
  "@@GITHUB_PAT@@": "gh" + "p_abcdefghijklmnopqrstuvwxyz0123456789",
};
const expand = (text) =>
  Object.entries(MARKERS).reduce((t, [k, v]) => t.split(k).join(v), text);

function measure(file) {
  const raw = require(file);
  const set = raw.cases.map((c) => ({
    ...c,
    text: expand(c.text),
    must_not_contain: c.must_not_contain.map(expand),
  }));
  const leakCases = set.filter((c) => c.expect === "leak");
  const missed = leakCases.filter((c) => leaked(c).length).map((c) => c.text);
  const changed = set
    .filter(
      (c) => c.expect === "unchanged" && redact.redactText(c.text) !== c.text,
    )
    .map((c) => c.text);
  return { total: leakCases.length, missed, changed };
}

describe("held-out sets (written separately from the patterns)", () => {
  // Sets 1 and 2 were used to extend the patterns, so they are regression gates, not estimates.
  // Set 3 was written afterwards and measured once: it is the only unbiased number. Its ceiling
  // is the measured value; if the patterns improve, lower it. Do not tune against it.
  it.each([
    ["./redaction-heldout.json", 0, "tuned on"],
    ["./redaction-heldout-2.json", 1, "tuned on"],
    ["./redaction-heldout-3.json", 7, "measured once, not tuned"],
  ])(
    "%s: at most %i leaks, no false positives (%s)",
    (file, ceiling, label) => {
      const m = measure(file);
      console.log(
        `redaction ${file}: ${m.missed.length}/${m.total} leak cases missed ` +
          `(${((100 * m.missed.length) / m.total).toFixed(1)}%), ${m.changed.length} false positives [${label}]`,
      );
      expect(m.changed).toEqual([]);
      expect(m.missed.length).toBeLessThanOrEqual(ceiling);
    },
  );
});

describe("counts and urls", () => {
  it("counts what it masked, by kind", () => {
    const counts = redact.newCounts();
    redact.redactText("a@b.com and c@d.org, call 9876543210", counts);
    expect(counts.email).toBe(2);
    expect(counts.phone).toBe(1);
  });

  it("removes credentials, fragments, query values and long ids from addresses", () => {
    const counts = redact.newCounts();
    const out = redact.redactUrl(
      "https://user:pw@shop.example.com/orders/1234567890/view?token=abc&email=a@b.com#frag",
      counts,
    );
    expect(out).toBe(
      "https://shop.example.com/orders/:id/view?token=[V]&email=[V]",
    );
    expect(out).not.toMatch(/pw|abc|a@b\.com|frag|1234567890/);
  });

  it("never throws on junk", () => {
    expect(redact.redactUrl("not a url")).toBe("[URL]");
    expect(redact.redactText(null)).toBe("");
    expect(redact.redactUrl("javascript:alert(1)")).toBe("javascript:");
  });
});
