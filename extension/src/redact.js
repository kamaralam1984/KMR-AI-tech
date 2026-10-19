/*
 * Local redaction. Everything the agent collects passes through here BEFORE it leaves the page's
 * tab, and the server checks it again. It is pattern-based: it catches emails, phone and card
 * numbers, ID numbers, tokens, labelled personal details and a few name forms. It cannot catch a
 * name or address written in an ordinary sentence. The test set (test/redaction-cases.json)
 * measures that gap and the documentation reports it.
 */
"use strict";

(function () {
  const VERSION = "1";

  const PATTERNS = [
    ["token", /\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]*/g, "[TOKEN]"],
    ["token", /\b(?:bearer|basic)\s+[\w.~+/=-]{8,}/gi, "[TOKEN]"],
    [
      "token",
      /\b([a-z_]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|auth|session|otp|cvv|cvc)[a-z_]*)\s*[=:]\s*["']?[^\s"',;&]{3,}/gi,
      "$1=[TOKEN]",
    ],
    ["email", /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "[EMAIL]"],
    ["card", /\b(?:\d[ -]?){13,19}\b/g, "[CARD]"],
    ["id", /\b\d{4}[ -]?\d{4}[ -]?\d{4}\b/g, "[ID]"],
    ["id", /\b[A-Z]{5}\d{4}[A-Z]\b/g, "[ID]"],
    ["id", /\b[A-Z]{4}0[A-Z0-9]{6}\b/g, "[ID]"],
    [
      "personal",
      /\b(full name|first name|last name|name|dob|date of birth|address|father|mother|mobile|phone|email|aadhaar|aadhar)\s*[:=-]\s*[^\n,;|]{2,60}/gi,
      "$1: [PERSONAL]",
    ],
    [
      "name",
      /\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Sri)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?/g,
      "[NAME]",
    ],
    [
      "code",
      /\b(otp|code|pin|cvv|cvc|passcode)\b([^\n]{0,20}?)\b\d{4,8}\b/gi,
      "$1$2[CODE]",
    ],
    // Obfuscated emails: "name at example dot com", "name(at)example(dot)com".
    [
      "email",
      /\b[\w]+(?:\s*(?:\(dot\)|\[dot\]|\sdot\s)\s*[\w]+)*\s*(?:\(at\)|\[at\]|\sat\s)\s*[\w]+(?:\s*(?:\(dot\)|\[dot\]|\sdot\s)\s*[\w]+)+/gi,
      "[EMAIL]",
      /(?:\(at\)|\[at\]|\sat\s)[\s\S]{0,60}(?:\(dot\)|\[dot\]|\sdot\s)/i,
    ],
    ["id", /\b\d{3}-\d{2}-\d{4}\b/g, "[ID]"],
    ["id", /\b[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}\b/g, "[ID]"],
    ["id", /\b[A-Z]\d{7}\b/g, "[ID]"],
    ["ip", /\b(?:\d{1,3}\.){3}\d{1,3}\b/g, "[IP]"],
    // Dates of birth, only when something says it is one.
    [
      "dob",
      /\b(born|birth(?:day)?|birth date|dob|d\.o\.b\.?)\b(?:\s+(?:on|is|date))*[\s:=-]*(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})/gi,
      "$1: [DOB]",
    ],
    [
      "dob",
      /\bdate of birth\b(?:\s+(?:is|on))?[\s:=-]*(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})/gi,
      "date of birth: [DOB]",
    ],
    // Addresses.
    [
      "address",
      /\b(?:flat|house|plot|door|h\.?\s?no\.?)\s*(?:no\.?\s*)?#?\d+[A-Za-z]?\b[^.\n]{0,60}/gi,
      "[ADDRESS]",
    ],
    [
      "address",
      /\b\d{1,4}[A-Za-z]?,?\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}\s+(?:Street|St|Road|Rd|Lane|Nagar|Marg|Avenue|Ave|Colony|Layout|Sector)\b(?:\s+[A-Z][a-z]+){0,2}/g,
      "[ADDRESS]",
    ],
    [
      "pin",
      /\b(pin ?code|pin|zip(?: code)?|postal code|post ?code)\b\D{0,12}\d{4,6}(?:-\d{4})?/gi,
      "$1 [PIN]",
    ],
    ["pin", /(?<![\d.,])\d{6}(?![\d.,])/g, "[PIN]"],
    // Handles and usernames.
    [
      "user",
      /\b(signed in as|logged in as|log(?:ged)? in as|username|user name|user id|userid|login id|handle)\s*[:=-]?\s*@?[\w.]{3,40}/gi,
      "$1: [USER]",
    ],
    ["user", /(?<![\w@])@[A-Za-z0-9_]{3,30}\b/g, "@[USER]"],
    ["token", /\bAKIA[0-9A-Z]{16}\b/g, "[TOKEN]"],
    ["token", /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b/g, "[TOKEN]"],
    ["token", /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g, "[TOKEN]"],
    ["token", /\bsk-[A-Za-z0-9_-]{20,}\b/g, "[TOKEN]"],
    ["token", /\bAIza[0-9A-Za-z_-]{35}\b/g, "[TOKEN]"],
    ["code", /\b(cvv|cvc|cid)\b\D{0,10}\d{3,4}\b/gi, "$1 [CODE]"],
    // Emails written with spaces around the @ and dots.
    [
      "email",
      /[\w+-]+(?:\s*\.\s*[\w-]+)*\s+@\s+[\w-]+(?:\s*\.\s*[\w-]+)+/g,
      "[EMAIL]",
      /\s@\s/,
    ],
    // UPI ids and similar "name@bank" handles (no dot after the @).
    ["id", /\b[A-Za-z0-9._-]{2,}@[a-z]{3,}\b(?![.\w])/g, "[ID]"],
    ["id", /\b[A-Z]{3}\d{7}\b/g, "[ID]"],
    ["id", /\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b/g, "[ID]"],
    [
      "ip",
      /(?<![\w:])(?=[0-9A-Fa-f:]*(?:[A-Fa-f]|::))(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])/g,
      "[IP]",
    ],
    // More address shapes.
    ["address", /\b\d{1,4}\s*\/\s*[A-Za-z0-9]+,\s*[^.\n]{3,60}/g, "[ADDRESS]"],
    [
      "address",
      /\b\d{1,2}(?:st|nd|rd|th)\s+floor\b[^.\n]{0,50}/gi,
      "[ADDRESS]",
    ],
    [
      "address",
      /\b(?:[Vv]illage|[Vv]ill\.?|[Pp]ost|[Dd]istrict|[Tt]ehsil|[Tt]aluka|[Mm]andal)\s+[A-Z][a-z]+/g,
      "[ADDRESS]",
    ],
    [
      "address",
      /\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:Apartments?|Heights|Towers?|Residency|Society|Enclave|Complex|Park|Villas?|Gardens?)\b/g,
      "[ADDRESS]",
    ],
    ["token", /\b(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}\b/g, "[TOKEN]"],
  ];

  const PHONE = /(?<![\w.])\+?\d[\d\s().-]{8,}\d(?![\w])/g;
  const DATE_LIKE = /^\d{4}-\d{2}-\d{2}/;
  const DIGIT_WORDS = "zero|oh|one|two|three|four|five|six|seven|eight|nine";
  const SPELLED = new RegExp(
    `\\b(?:(?:${DIGIT_WORDS})(?:[\\s,-]+|\\b)){4,}`,
    "gi",
  );

  // Words that follow a greeting or "by" but are not people.
  const NOT_NAMES = new Set(
    (
      "support team us service services store page home cart order orders account settings help sale " +
      "terms policy privacy conditions products product contact customer care desk admin administrator " +
      "sorry happy great sure glad ready afraid fine good well welcome back all today tomorrow yesterday there everyone again back now soon here this that the our your my " +
      "free shipping delivery payment checkout login signup search menu news blog about faq error warning " +
      "success cancel save submit continue next previous close open view more less new old " +
      "monday tuesday wednesday thursday friday saturday sunday january february march april may june july " +
      "august september october november december"
    ).split(" "),
  );
  const STRONG_CUES =
    "hello|hi|hey|dear|welcome back|thanks|thank you|regards|best regards|sincerely|cheers|my name is|name is|mera naam|mera naam hai|contact person|beneficiary|nominee|guardian|father's name|mother's name|spouse|invoice for|bill to|billed to|ship to|deliver to|attn|attention|student|applicant|candidate|patient|passenger|tenant|employee|account holder";
  // Common words too: only treated as a cue when two or more Capitalized words follow.
  const WEAK_CUES =
    "from|to|by|cc|ask|ask for|tell|told|call|called|meet|met|contact|customer|member|guest|owner|holder|i am|this is|name of|application of|booking for|ticket for|raised by|signed by|submitted by|assigned to|welcome|sent to|send to|for";
  // Cues match in any letter case; the NAME after them must be Capitalized Words (no /i flag).
  const flexible = (word) =>
    word
      .split("")
      .map((c) =>
        /[a-z]/i.test(c) ? `[${c.toUpperCase()}${c.toLowerCase()}]` : c,
      )
      .join("");
  const cueGroup = (words) => `(?:${words.split("|").map(flexible).join("|")})`;
  const NAME1 = "[A-Z][a-z]{2,}(?:\\s+[A-Z][a-z]{1,}){0,2}";
  const NAME2 = "[A-Z][a-z]{2,}(?:\\s+[A-Z][a-z]{1,}){1,2}";
  const SEP = "(\\s*[,:-]?\\s+)";
  const STRONG_RE = new RegExp(
    `\\b(${cueGroup(STRONG_CUES)})${SEP}(${NAME1})(?![a-z])`,
    "g",
  );
  const WEAK_RE = new RegExp(
    `\\b(${cueGroup(WEAK_CUES)})${SEP}(${NAME2})(?![a-z])`,
    "g",
  );
  const NAME_BEFORE_VERB = new RegExp(
    `\\b([A-Z][a-z]{2,}\\s+[A-Z][a-z]{2,})(\\s+(?:called|said|asked|wrote|emailed|replied|requested|submitted|applied|booked|paid|signed|logged|complained|confirmed|phoned|visited|scored|passed|failed|enrolled|admitted|attended|joined|reported|reached)\\b)`,
    "g",
  );
  // Hinglish: "Rahul ne bola", "Priya ka form", "Amit ko bhejo".
  const HINGLISH = /\b([A-Z][a-z]{2,})(\s+(?:ne|ka|ki|ko)\s+[a-z]+)/g;

  function maskNames(text, counts) {
    const ok = (name) =>
      !name.split(/\s+/).some((w) => NOT_NAMES.has(w.toLowerCase()));
    const swap = (match, cue, sep, name) => {
      if (!ok(name)) return match;
      bump(counts, "name");
      return `${cue}${sep}[NAME]`;
    };
    text = text.replace(STRONG_RE, swap).replace(WEAK_RE, swap);
    text = text.replace(HINGLISH, (match, name, rest) => {
      if (!ok(name)) return match;
      bump(counts, "name");
      return `[NAME]${rest}`;
    });
    return text.replace(NAME_BEFORE_VERB, (match, name, rest) => {
      if (!ok(name)) return match;
      bump(counts, "name");
      return `[NAME]${rest}`;
    });
  }

  const MAX_TEXT = 300;

  function newCounts() {
    return {};
  }
  function bump(counts, kind) {
    if (counts) counts[kind] = (counts[kind] || 0) + 1;
  }

  function redactText(input, counts, limit = MAX_TEXT) {
    // Cap the work before doing it: a huge text node must not make the page slow.
    let text = String(input == null ? "" : input)
      .slice(0, Math.max(limit * 4, 2000))
      .replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, " ");
    text = text.replace(/https?:\/\/[^\s"'<>)\]]+/g, (u) =>
      redactUrl(u, counts),
    );
    for (const [kind, pattern, replacement, guard] of PATTERNS) {
      if (guard && !guard.test(text)) continue; // cheap pre-check for the costlier rules
      text = text.replace(pattern, (...args) => {
        bump(counts, kind);
        // Expand $1/$2 manually so replacement strings work with a function replacer.
        return replacement.replace(/\$(\d)/g, (_, n) => args[Number(n)] ?? "");
      });
    }
    text = text.replace(PHONE, (run) => {
      const digits = run.replace(/\D/g, "").length;
      if (digits >= 10 && !DATE_LIKE.test(run)) {
        bump(counts, "phone");
        return "[PHONE]";
      }
      return run;
    });
    text = text.replace(SPELLED, () => {
      bump(counts, "spelled");
      return "[SPELLED-NUMBER] ";
    });
    text = maskNames(text, counts);
    return text.replace(/\s+/g, " ").trim().slice(0, limit);
  }

  /** Drops credentials and fragment, removes every query value, masks long numeric path parts. */
  function redactUrl(input, counts) {
    let url;
    try {
      url = new URL(String(input));
    } catch {
      return "[URL]";
    }
    if (url.protocol !== "http:" && url.protocol !== "https:")
      return `${url.protocol}`;
    if (url.username || url.password) bump(counts, "token");
    const rawParts = url.pathname.split("/");
    const path = rawParts
      .map((part, i) => {
        if (
          i > 1 &&
          /^(u|user|users|profile|profiles|member|members|~[\w.-]*)$/i.test(
            rawParts[i - 1],
          ) &&
          part
        ) {
          bump(counts, "user");
          return ":user";
        }
        if (part.startsWith("@") && part.length > 1) {
          bump(counts, "user");
          return "@user";
        }
        if (
          /^\d{5,}$/.test(part) ||
          /^[0-9a-f]{16,}$/i.test(part) ||
          /^[\w-]{24,}$/.test(part)
        ) {
          bump(counts, "id");
          return ":id";
        }
        if (part.includes("@")) {
          bump(counts, "email");
          return ":email";
        }
        return part;
      })
      .join("/");
    const params = [...url.searchParams.keys()];
    if (params.length) bump(counts, "url_value");
    const query = params.length
      ? "?" + params.map((k) => `${encodeURIComponent(k)}=[V]`).join("&")
      : "";
    return `${url.protocol}//${url.host}${path}${query}`.slice(0, 500);
  }

  const api = { VERSION, newCounts, redactText, redactUrl, MAX_TEXT };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else (globalThis.KMR_EXT = globalThis.KMR_EXT || {}).redact = api;
})();
