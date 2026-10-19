"""Joins what the browser saw with backend evidence, and says only what that evidence supports.

Three kinds of evidence, each labeled by where it came from:
  * browser_observed   - failures the scanner's browser saw
  * api_observed       - API checks KMR AI itself ran against the verified host
  * customer_supplied  - backend logs the customer uploaded, matched by request id

The result is one of four classifications. None of them is a root cause. When the evidence is
missing or points both ways the answer is "needs review", never a guess.
"""

from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

Classification = Literal["backend_confirmed", "uncertain", "no_backend_data", "third_party"]
BROWSER, API, LOGS = "browser_observed", "api_observed", "customer_supplied"
_ERROR_LEVELS = {"error", "critical"}


@dataclass(frozen=True)
class Obs:
    id: str
    kind: str
    severity: str
    page_url: str | None
    data: dict[str, Any]


@dataclass(frozen=True)
class Run:
    id: str
    check_name: str
    path: str
    status: str
    http_status: int | None
    reasons: list[dict[str, Any]]


@dataclass(frozen=True)
class Log:
    id: str
    request_id: str | None
    http_status: int | None
    level: str | None
    path: str | None
    message: str | None


@dataclass
class Evidence:
    source: str
    ref_type: str
    ref_id: str
    summary: str


@dataclass
class Correlation:
    observation_id: str
    kind: str
    page_url: str | None
    request_url: str | None
    http_status: int | None
    request_id: str | None
    classification: Classification
    needs_review: bool
    statement: str
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return sorted({BROWSER, *(e.source for e in self.evidence)})


def _path_of(url: str | None) -> str | None:
    return (urlsplit(url).path or "/") if url else None


def _log_failed(log: Log) -> bool:
    return (log.http_status or 0) >= 500 or (log.level or "") in _ERROR_LEVELS


def _failed_run(run: Run) -> bool:
    return run.status != "passed"


def correlate(
    observations: list[Obs], runs: list[Run], logs: list[Log], *, site_host: str
) -> list[Correlation]:
    by_request: dict[str, list[Log]] = {}
    for log in logs:
        if log.request_id:
            by_request.setdefault(log.request_id, []).append(log)
    runs_by_path: dict[str, list[Run]] = {}
    for run in runs:
        runs_by_path.setdefault(_path_of(run.path) or "/", []).append(run)

    results: list[Correlation] = []
    for obs in observations:
        if obs.kind == "api_check":
            if obs.data.get("status") == "passed":
                continue
            results.append(_api_failure(obs))
            continue
        if obs.kind not in ("http_error", "failed_request", "page"):
            continue
        status = obs.data.get("status") if obs.kind in ("http_error", "page") else None
        if obs.kind == "page" and not (
            obs.data.get("error") or (isinstance(status, int) and status >= 400)
        ):
            continue
        request_url = obs.data.get("url") if obs.kind != "page" else obs.page_url
        url = str(request_url) if request_url else None
        host = urlsplit(url).hostname if url else None
        request_id = obs.data.get("request_id")
        correlation = Correlation(
            observation_id=obs.id,
            kind=obs.kind,
            page_url=obs.page_url,
            request_url=url,
            http_status=status if isinstance(status, int) else None,
            request_id=str(request_id) if request_id else None,
            classification="no_backend_data",
            needs_review=True,
            statement="",
        )
        correlation.evidence.append(
            Evidence(BROWSER, "observation", obs.id, _browser_summary(obs, status))
        )
        if host and host != site_host:
            correlation.classification, correlation.needs_review = "third_party", False
            correlation.statement = (
                f"This request went to {host}, which is not your verified site, so no backend "
                "evidence can be collected for it."
            )
            results.append(correlation)
            continue

        matched_logs = by_request.get(correlation.request_id or "", [])
        matched_runs = runs_by_path.get(_path_of(url) or "", [])
        for log in matched_logs[:3]:
            correlation.evidence.append(Evidence(LOGS, "log_entry", log.id, _log_summary(log)))
        for run in matched_runs[:2]:
            correlation.evidence.append(Evidence(API, "api_check_run", run.id, _run_summary(run)))

        browser_status = correlation.http_status
        backend_failed = any(_log_failed(log) for log in matched_logs) or any(
            _failed_run(run) for run in matched_runs
        )
        both_agree = browser_status is not None and any(
            log.http_status == browser_status for log in matched_logs
        )
        if backend_failed or both_agree:
            correlation.classification, correlation.needs_review = "backend_confirmed", False
            correlation.statement = (
                "The browser and the backend both show a problem for this request. This is "
                "observed on both sides. It does not yet say why it happened."
            )
        elif matched_logs or matched_runs:
            correlation.classification = "uncertain"
            correlation.statement = (
                "The browser saw a failure, but the backend looked healthy "
                + ("in its logs" if matched_logs else "when checked")
                + ". It may be intermittent, or specific to the browser or the page. There is "
                "not enough evidence to name a cause."
            )
        else:
            correlation.statement = (
                "The browser saw a failure. No backend telemetry covers this request (no matching "
                "log and no API check for this path), so the backend can be neither blamed nor "
                "ruled out."
            )
        results.append(correlation)
    return results


def _api_failure(obs: Obs) -> Correlation:
    codes = ", ".join(str(r.get("code")) for r in obs.data.get("reasons", []))
    correlation = Correlation(
        observation_id=obs.id,
        kind="api_check",
        page_url=None,
        request_url=obs.page_url,
        http_status=obs.data.get("http_status"),
        request_id=obs.data.get("request_id"),
        classification="backend_confirmed",
        needs_review=False,
        statement=(
            "KMR AI called this API endpoint itself and it did not meet its contract. This is "
            "observed directly. It does not yet say why."
        ),
    )
    correlation.evidence.append(
        Evidence(API, "observation", obs.id, f"{obs.data.get('name')}: {codes or 'failed'}")
    )
    return correlation


def _browser_summary(obs: Obs, status: Any) -> str:
    if obs.kind == "failed_request":
        return f"The request failed in the browser ({obs.data.get('failure', 'network error')})."
    return f"The browser received HTTP {status}." if status else "The page could not be loaded."


def _log_summary(log: Log) -> str:
    bits = [b for b in (f"HTTP {log.http_status}" if log.http_status else None, log.level) if b]
    return "Backend log: " + (", ".join(bits) if bits else "entry with this request id")


def _run_summary(run: Run) -> str:
    codes = ", ".join(str(r.get("code")) for r in run.reasons)
    return f"API check '{run.check_name}' {run.status}" + (f" ({codes})" if codes else "")
