"""The built-in diagnosis provider: fixed rules, no AI.

It exists so diagnosis works, and can be tested and measured, without an AI provider. Everything
it says is derived from the evidence by explicit rules, and the product labels its output as
"built-in rules" so nobody mistakes it for a model. It only states what the evidence shows; where
the cause is unknown it says so and asks for review instead of guessing.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from app.diagnosis.schemas import (
    CorrelationItem,
    DiagnosisInput,
    DiagnosisOutput,
    EvidenceItem,
    Fact,
    Hypothesis,
    IssueDraft,
    ProviderResult,
    Usage,
)
from app.diagnosis.validate import fingerprint

PROVIDER = "rules"
MODEL = "built-in-rules"
CONFIG_VERSION = "rules-2"
_ORDER = ["info", "low", "medium", "high", "critical"]
_SLOW_MS = 3000

_NULL_READ = re.compile(
    r"Cannot read propert(?:y|ies) of (undefined|null)(?: \(reading '([^']+)'\))?"
)
_NOT_DEFINED = re.compile(r"\b([A-Za-z_$][\w$]*) is not defined")
_NOT_FUNCTION = re.compile(r"\b([A-Za-z_$][\w$.]*) is not a function")

_A11Y = {
    "images_missing_alt": (
        "low",
        "Images without alternative text",
        'Add a short alt attribute to each meaningful image, or alt="" for decorative ones.',
        "People using screen readers cannot tell what these images show.",
    ),
    "inputs_without_label": (
        "medium",
        "Form fields without a label",
        "Give every field a visible <label> connected with for/id, or an aria-label.",
        "People using screen readers, and anyone who taps a label to focus a field, cannot tell what to enter.",
    ),
    "buttons_without_name": (
        "medium",
        "Buttons without a name",
        "Give each button text or an aria-label that says what it does.",
        'Screen reader users hear only "button" and cannot tell what it does.',
    ),
    "links_without_name": (
        "medium",
        "Links without a name",
        "Give each link visible text or an aria-label that says where it goes.",
        "Screen reader users cannot tell where these links lead.",
    ),
    "missing_lang": (
        "low",
        "Page does not declare its language",
        'Add a lang attribute to the <html> element, for example lang="en".',
        "Screen readers may use the wrong pronunciation, and browsers cannot offer translation correctly.",
    ),
    "missing_title": (
        "low",
        "Page has no title",
        "Add a descriptive <title> to the page.",
        "Browser tabs, bookmarks and search results show no useful name for the page.",
    ),
    "heading_structure": (
        "info",
        "Page does not have exactly one main heading",
        "Use a single <h1> that names the page, and nest other headings below it.",
        "Headings help people and search engines understand the structure of the page.",
    ),
    "horizontal_overflow": (
        "medium",
        "Page is wider than the screen (sideways scrolling)",
        "Find the element wider than the viewport (fixed widths, large images, long unbroken "
        "text) and let it shrink with max-width: 100% or wrap.",
        "Visitors on narrow screens must scroll sideways, and content can be cut off.",
    ),
    "missing_viewport_meta": (
        "low",
        "No viewport setting for phones",
        'Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
        "On phones the page may be shown zoomed out and hard to read.",
    ),
}


@dataclass
class _Acc:
    category: str
    severity: str
    title: str
    component: str
    classification: Literal["diagnosed", "needs_review"] = "diagnosed"
    confidence: Literal["high", "medium", "low"] = "medium"
    reason: str | None = None
    summary: str = ""
    facts: list[Fact] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    impact: str = ""
    fix: str = ""
    verification: str = ""
    evidence_ids: list[str] = field(default_factory=list)


def _path(url: str | None) -> str:
    return (urlsplit(url).path or "/") if url else "/"


def _norm(path: str) -> str:
    return re.sub(r"\d+", "N", path)


def _more(a: str, b: str) -> str:
    return max(a, b, key=_ORDER.index)


class _Builder:
    def __init__(self) -> None:
        self.by_key: dict[str, _Acc] = {}

    def add(self, acc: _Acc) -> None:
        key = fingerprint(acc.category, acc.component, acc.title)
        current = self.by_key.get(key)
        if current is None:
            self.by_key[key] = acc
            return
        current.severity = _more(current.severity, acc.severity)
        current.facts = [*current.facts, *acc.facts][:12]
        current.hypotheses = [*current.hypotheses, *acc.hypotheses][:6]
        current.unverified = list(dict.fromkeys([*current.unverified, *acc.unverified]))[:6]
        current.evidence_ids = list(dict.fromkeys([*current.evidence_ids, *acc.evidence_ids]))[:50]
        if acc.classification == "needs_review":
            current.classification, current.confidence = "needs_review", "low"
            current.reason = current.reason or acc.reason


def _page_steps(path: str, extra: str) -> list[str]:
    return [f"Open {path} in a browser.", extra]


def diagnose_rules(inp: DiagnosisInput) -> DiagnosisOutput:
    build = _Builder()
    corr = {c.observation_id: c for c in inp.correlations}
    by_id = {e.id: e for e in inp.evidence}

    pages_by_message: dict[str, set[str]] = {}
    for ev in inp.evidence:
        if ev.kind in ("console_error", "page_error"):
            key = re.sub(r"\d+", "N", str(ev.data.get("message") or ""))[:120]
            pages_by_message.setdefault(key, set()).add(_path(ev.page_url))

    for ev in inp.evidence:
        d = ev.data
        if ev.kind in ("console_error", "page_error"):
            key = re.sub(r"\d+", "N", str(d.get("message") or ""))[:120]
            _script_error(build, ev, len(pages_by_message.get(key, ())))
        elif ev.kind == "http_error":
            _request_problem(build, ev, int(d.get("status") or 0), None, corr.get(ev.id), by_id)
        elif ev.kind == "failed_request":
            _request_problem(
                build, ev, 0, str(d.get("failure") or "network error"), corr.get(ev.id), by_id
            )
        elif ev.kind == "page":
            _page(build, ev, corr.get(ev.id))
        elif ev.kind == "a11y_issue":
            _a11y(build, ev)
        elif ev.kind == "api_check" and d.get("status") != "passed":
            _api_check(build, ev)

    order = sorted(build.by_key.values(), key=lambda a: (-_ORDER.index(a.severity), a.title))
    return DiagnosisOutput(
        issues=[
            IssueDraft(
                title=a.title[:120],
                category=a.category,
                severity=a.severity,
                component=a.component[:200],
                summary=a.summary[:600],
                facts=a.facts[:12],
                hypotheses=a.hypotheses[:6],
                unverified=[u[:300] for u in a.unverified[:6]],
                reproduction_steps=[s[:300] for s in a.steps[:10]],
                impact=a.impact[:400],
                proposed_fix=a.fix[:600],
                verification_plan=a.verification[:400],
                confidence=a.confidence,
                classification=a.classification,
                needs_review_reason=a.reason,
                evidence_ids=a.evidence_ids[:50],
            )
            for a in order[:30]
        ]
    )


_CORS = re.compile(r"blocked by CORS policy", re.IGNORECASE)
_MIXED = re.compile(r"^Mixed Content", re.IGNORECASE)
_CSP = re.compile(r"Content Security Policy", re.IGNORECASE)
_TLS = re.compile(r"net::ERR_(?:CERT|SSL|TLS)\w*|ERR_SSL_\w+|ERR_CERT_\w+")
_ACTIVE_RESOURCE = re.compile(
    r"\b(script|stylesheet|frame|iframe|xhr|fetch|font|worker)\b", re.IGNORECASE
)


def _signature_issue(ev: EvidenceItem, message: str, path: str) -> _Acc | None:
    """Well-known browser policy and transport errors, which are not "script bugs"."""
    fact = [Fact(text=f"The browser reported: {message}", evidence_ids=[ev.id])]
    if _CORS.search(message):
        return _Acc(
            category="backend_api",
            severity="medium",
            classification="needs_review",
            confidence="low",
            reason="The browser blocked a cross-origin call. Only the server's response headers can confirm why, and none were collected.",
            title=f"A cross-origin request is blocked by CORS on {path}",
            component=path,
            summary="A call from this page to another origin was refused by the browser.",
            facts=fact,
            unverified=["Which CORS header is missing or wrong, on the server that was called."],
            steps=_page_steps(path, "Trigger the action and read the console message."),
            impact="Whatever depends on that call does not work for visitors.",
            fix="Make the called server send Access-Control-Allow-Origin for your site's origin, and answer preflight OPTIONS requests.",
            verification="Repeat the action and confirm the console error is gone.",
            evidence_ids=[ev.id],
        )
    if _MIXED.search(message):
        return _Acc(
            category="content",
            severity="medium" if _ACTIVE_RESOURCE.search(message) else "low",
            title="Page loads insecure (http) resources on a secure page",
            component=path,
            confidence="high",
            summary="A page served over HTTPS asks for something over plain HTTP.",
            facts=fact,
            unverified=["Whether the secure version of that address exists."],
            steps=_page_steps(path, "Find the http:// address named in the console message."),
            impact="Browsers block or warn about this, so part of the page may be missing.",
            fix="Change those addresses to https:// (or relative addresses).",
            verification="Reload the page and confirm the warning is gone.",
            evidence_ids=[ev.id],
        )
    if _CSP.search(message):
        return _Acc(
            category="other",
            severity="medium" if _ACTIVE_RESOURCE.search(message) else "low",
            title="Content Security Policy blocks something on the page",
            component=path,
            confidence="high",
            summary="The page's security policy refused to load or run something.",
            facts=fact,
            unverified=["Whether the blocked item is wanted, or should have been removed."],
            steps=_page_steps(path, "Read the policy directive named in the console message."),
            impact="The blocked item does not work, and the policy may be too strict or the page too permissive.",
            fix="Either allow that source in the policy, or remove the reference from the page.",
            verification="Reload the page and confirm the message is gone.",
            evidence_ids=[ev.id],
        )
    if _TLS.search(message):
        return _Acc(
            category="network",
            severity="high",
            title="Certificate or secure-connection error",
            component=path,
            confidence="high",
            summary="The browser could not establish a secure connection.",
            facts=fact,
            unverified=["Which certificate or server is at fault."],
            steps=_page_steps(path, "Open the address and look at the certificate warning."),
            impact="Visitors may see a security warning or be unable to reach the site.",
            fix="Check the certificate's dates, chain and host name, and the TLS settings of the server.",
            verification="Open the site in a fresh browser and confirm there is no warning.",
            evidence_ids=[ev.id],
        )
    return None


def _script_error(build: _Builder, ev: EvidenceItem, pages_with_message: int = 1) -> None:
    message = str(ev.data.get("message") or "Unknown error")
    path = _path(ev.page_url)
    special = _signature_issue(ev, message, path)
    if special is not None:
        build.add(special)
        return
    hypotheses: list[Hypothesis] = []
    fix = (
        "Open the browser console on this page, find the line that throws, and handle the case "
        "that triggers it."
    )
    if m := _NULL_READ.search(message):
        prop = f" '{m.group(2)}'" if m.group(2) else ""
        hypotheses.append(
            Hypothesis(
                text=f"The code reads{prop} from a value that was {m.group(1)}. The value may not be "
                "set yet, or may be missing from the data the page received.",
                confidence="medium",
                evidence_ids=[ev.id],
            )
        )
        fix = (
            "Check that the value exists before using it (for example with optional chaining), "
            "and find out why it was missing on this page."
        )
    elif m := _NOT_DEFINED.search(message):
        hypotheses.append(
            Hypothesis(
                text=f"'{m.group(1)}' is used before it exists, or the script that defines it did not "
                "load.",
                confidence="medium",
                evidence_ids=[ev.id],
            )
        )
        fix = f"Make sure the script that defines '{m.group(1)}' loads before it is used."
    elif m := _NOT_FUNCTION.search(message):
        hypotheses.append(
            Hypothesis(
                text=f"'{m.group(1)}' was called as a function but is something else at that moment.",
                confidence="low",
                evidence_ids=[ev.id],
            )
        )
    uncaught = ev.kind == "page_error"
    shared = pages_with_message >= 2
    # The same error on several pages usually has one cause (a shared script): one issue, with
    # every page's evidence, rather than one per page.
    build.add(
        _Acc(
            category="frontend_error",
            severity="high" if uncaught else "medium",
            title=(
                f"JavaScript error on {pages_with_message} pages: {message[:70]}"
                if shared
                else f"JavaScript error on {path}: {message[:70]}"
            ),
            component=f"{pages_with_message} pages" if shared else path,
            confidence="medium" if hypotheses else "high",
            summary="A script on this page raised an error while it loaded.",
            facts=[Fact(text=f"On {path} the browser reported: {message}", evidence_ids=[ev.id])],
            hypotheses=hypotheses,
            unverified=["Which exact line of your source code raises it."]
            + (["Whether it comes from one shared script."] if shared else []),
            steps=_page_steps(
                path, "Open the browser developer tools console and look for the error."
            ),
            impact="Part of the page may not work for visitors, depending on what the script does.",
            fix=fix,
            verification="Reload the page and confirm the error is gone, then scan again.",
            evidence_ids=[ev.id],
        )
    )


def _request_problem(
    build: _Builder,
    ev: EvidenceItem,
    status: int,
    failure: str | None,
    corr: CorrelationItem | None,
    by_id: dict[str, EvidenceItem],
) -> None:
    url = str(ev.data.get("url") or "")
    host, path = (urlsplit(url).hostname or ""), _path(url)
    resource = str(ev.data.get("resource_type") or "request")
    ids = [ev.id]
    if corr is not None:
        ids += [i for i in corr.evidence_ids if i in by_id and i != ev.id]
    third_party = corr is not None and corr.classification == "third_party"

    if third_party:
        build.add(
            _Acc(
                category="network",
                severity="medium" if resource in ("script", "stylesheet") else "low",
                title=f"A {resource} from another website failed to load ({host})",
                component=host,
                confidence="high",
                summary="A resource hosted outside your site did not load.",
                facts=[
                    Fact(
                        text=f"The browser could not load {url}"
                        + (f" (HTTP {status})." if status else f" ({failure})."),
                        evidence_ids=[ev.id],
                    )
                ],
                unverified=["Whether the third-party service had an outage."],
                impact="Anything on the page that depends on this resource may be missing or broken.",
                fix="Check the address is correct and the service is up. Consider a fallback.",
                verification="Reload the page and confirm the resource loads.",
                evidence_ids=ids,
            )
        )
        return

    server = status >= 500 or failure is not None
    if server:
        confirmed = corr is not None and corr.classification == "backend_confirmed"
        title = (
            f"Server error (HTTP {status}) from {path}"
            if status
            else f"Request to {path} failed ({failure})"
        )
        facts = [
            Fact(
                text=f"The browser received HTTP {status} for {url}"
                if status
                else f"The browser could not complete the request to {url}: {failure}",
                evidence_ids=[ev.id],
            )
        ]
        if corr is not None:
            for ref in corr.evidence_ids:
                other = by_id.get(ref)
                if other is not None and other.id != ev.id:
                    facts.append(Fact(text=_evidence_fact(other), evidence_ids=[other.id]))
        acc = _Acc(
            category="backend_api" if status else "network",
            severity="critical" if resource == "document" and status >= 500 else "high",
            title=title,
            component=path,
            facts=facts[:6],
            evidence_ids=ids,
            summary="A request to your own site failed on the server side or on the way there.",
            impact="Features that depend on this request do not work for visitors.",
            steps=_page_steps(
                path, f"Trigger the action that calls {path} and watch the network tab."
            ),
            verification="Repeat the request and confirm it succeeds, then scan again.",
            unverified=["Why the server failed. This needs server-side investigation."],
        )
        if confirmed:
            acc.confidence = "medium"
            acc.fix = (
                "Look in your server logs for the request ID shown with this issue, find the "
                "error behind it, and fix that."
            )
        else:
            acc.classification, acc.confidence = "needs_review", "low"
            acc.reason = (
                corr.statement if corr else None
            ) or "No backend evidence is available for this request."
            acc.fix = (
                "Connect backend evidence (an API check for this path, or uploaded server "
                "logs) so the failure can be confirmed and explained."
            )
        build.add(acc)
        return

    # 4xx
    api_call = resource in ("fetch", "xhr")
    severity = (
        "medium"
        if (api_call or resource in ("script", "stylesheet", "document", "font"))
        else "low"
    )
    kind_word = {
        "image": "image",
        "script": "script",
        "stylesheet": "stylesheet",
        "font": "font",
        "fetch": "API call",
        "xhr": "API call",
    }.get(resource, "resource")
    # Broken files are grouped by kind and status (the facts list every address); API calls stay
    # apart per path because each endpoint can fail for its own reason.
    plural = {
        "image": "Images",
        "script": "Scripts",
        "stylesheet": "Stylesheets",
        "font": "Fonts",
    }.get(resource, "Resources")
    build.add(
        _Acc(
            category="backend_api" if api_call else "content",
            severity=severity,
            title=f"A {kind_word} returns HTTP {status}: {path}"
            if api_call
            else f"{plural} return HTTP {status}",
            component=path if api_call else plural.lower(),
            confidence="high" if not api_call else "medium",
            classification="needs_review"
            if api_call and not (corr and corr.classification == "backend_confirmed")
            else "diagnosed",
            reason="The call was rejected, but there is no backend evidence about why."
            if api_call
            else None,
            summary=f"The page asks for something that {'the API rejects' if api_call else 'is not there'}.",
            facts=[
                Fact(text=f"The browser received HTTP {status} for {url}", evidence_ids=[ev.id])
            ],
            unverified=["Whether the address is wrong, or the item was removed."]
            if not api_call
            else ["Why the API rejected the call."],
            impact="The page shows a gap or an error where this should be.",
            fix=(
                "Correct the address the page uses, or restore the missing file."
                if not api_call
                else "Check what the call sends and what the endpoint expects."
            ),
            verification="Reload the page and confirm the request now succeeds.",
            evidence_ids=ids,
        )
    )


def _evidence_fact(item: EvidenceItem) -> str:
    d = item.data
    if item.type == "api_check_run":
        reasons = ", ".join(str(r.get("code")) for r in d.get("reasons", []) if isinstance(r, dict))
        return f"KMR AI's API check '{d.get('name')}' {d.get('status')}" + (
            f" ({reasons})." if reasons else "."
        )
    if item.type == "log_entry":
        return (
            f"Your backend log shows HTTP {d.get('http_status') or '?'}"
            f" ({d.get('level') or 'no level'}) for this request."
        )
    return "Related observation."


def _page(build: _Builder, ev: EvidenceItem, corr: CorrelationItem | None) -> None:
    d = ev.data
    path = _path(ev.page_url)
    status = d.get("status")
    if d.get("left_site"):
        return
    if d.get("error"):
        build.add(
            _Acc(
                category="network",
                severity="high",
                title=f"Page could not be loaded: {path}",
                component=path,
                confidence="high",
                summary="The scanner could not load this page.",
                facts=[Fact(text=f"Loading {path} failed: {d.get('error')}", evidence_ids=[ev.id])],
                unverified=["Whether the problem is temporary or on every visit."],
                impact="Visitors may not be able to open this page.",
                fix="Open the page yourself and check it loads; look at server availability and timeouts.",
                verification="Scan again and confirm the page loads.",
                evidence_ids=[ev.id],
            )
        )
        return
    if isinstance(status, int) and status >= 400:
        server = status >= 500
        build.add(
            _Acc(
                category="backend_api" if server else "content",
                severity="critical" if server else "medium",
                title=f"Page returns HTTP {status}: {path}",
                component=path,
                confidence="high" if not server else "medium",
                classification="needs_review"
                if server and not (corr and corr.classification == "backend_confirmed")
                else "diagnosed",
                reason="The server failed, and no backend evidence explains it."
                if server
                else None,
                summary="A page on your site does not load normally.",
                facts=[Fact(text=f"{path} answered with HTTP {status}.", evidence_ids=[ev.id])],
                unverified=["Why it answers this way."],
                impact="Visitors who open this page see an error.",
                fix="Check the server logs for this path."
                if server
                else "Fix the link, or restore the page.",
                verification="Open the page and confirm it loads.",
                evidence_ids=[ev.id],
            )
        )
    load_ms = d.get("load_ms")
    if isinstance(load_ms, int | float) and load_ms > _SLOW_MS:
        build.add(
            _Acc(
                category="performance",
                severity="low",
                title=f"Slow page load: {path}",
                component=path,
                confidence="medium",
                summary="The page took a long time to finish loading.",
                facts=[
                    Fact(
                        text=f"{path} took {int(load_ms)} ms to load in the scanner's browser.",
                        evidence_ids=[ev.id],
                    )
                ],
                unverified=["Whether visitors on real networks see the same delay."],
                impact="Slow pages lose visitors.",
                fix="Find the largest or slowest requests on this page and reduce them.",
                verification="Scan again and compare the load time.",
                evidence_ids=[ev.id],
            )
        )


def _a11y(build: _Builder, ev: EvidenceItem) -> None:
    check = str(ev.data.get("check"))
    if check not in _A11Y:
        return
    severity, title, fix, impact = _A11Y[check]
    path = _path(ev.page_url)
    count = ev.data.get("count")
    build.add(
        _Acc(
            category="accessibility",
            severity=severity,
            title=title,
            component="all pages",
            confidence="high",
            summary="Found by checking the page structure, not by guessing.",
            facts=[Fact(text=f"On {path}: {count} occurrence(s).", evidence_ids=[ev.id])],
            impact=impact,
            fix=fix,
            steps=[f"Open {path} and inspect the elements involved."],
            verification="Scan again and confirm the count drops to zero.",
            evidence_ids=[ev.id],
        )
    )


def _api_check(build: _Builder, ev: EvidenceItem) -> None:
    d = ev.data
    name, path = str(d.get("name") or "API check"), _path(str(ev.page_url or ""))
    reasons = [r for r in d.get("reasons", []) if isinstance(r, dict)]
    codes = [str(r.get("code")) for r in reasons]
    http = d.get("http_status")
    server_side = (
        (isinstance(http, int) and http >= 500) or "too_slow" in codes or d.get("status") == "error"
    )
    parts = []
    for r in reasons:
        code = r.get("code")
        if code == "status_mismatch":
            parts.append(f"expected HTTP {r.get('expected')} but got {r.get('actual')}")
        elif code == "too_slow":
            parts.append(f"took {r.get('actual_ms')} ms (limit {r.get('max_ms')} ms)")
        elif code == "schema_mismatch":
            parts.append("the response shape did not match the expected schema")
        else:
            parts.append(str(code).replace("_", " "))
    build.add(
        _Acc(
            category="backend_api",
            severity="high" if server_side else "medium",
            title=f"API endpoint {path} does not meet its contract"
            if not (isinstance(http, int) and http >= 500)
            else f"Server error (HTTP {http}) from {path}",
            component=path,
            confidence="high",
            summary="KMR AI called this endpoint itself and compared the answer with what you expect.",
            facts=[
                Fact(
                    text=f"API check '{name}': " + ("; ".join(parts) or "failed") + ".",
                    evidence_ids=[ev.id],
                )
            ],
            unverified=["Why the endpoint behaves this way."],
            impact="Anything that calls this endpoint may break or misbehave.",
            steps=[f"Call {path} and compare the response with your contract."],
            fix="Compare the endpoint's behavior with the contract you set, and fix whichever is wrong.",
            verification="Run the API check again and confirm it passes.",
            evidence_ids=[ev.id],
        )
    )


class RulesProvider:
    name = PROVIDER
    model = MODEL
    config_version = CONFIG_VERSION

    async def diagnose(self, inp: DiagnosisInput) -> ProviderResult:
        started = time.perf_counter()
        output = diagnose_rules(inp)
        del started
        return ProviderResult(output=output, usage=Usage(), model=MODEL)
