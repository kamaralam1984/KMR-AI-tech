"""Business-impact ranking of findings (a labeled heuristic, never a measurement).

Without analytics access nothing here knows how many visitors a page gets or what it earns. The
rank comes from two things only: how severe the finding is, and what kind of journey the affected
page looks like (checkout, login, sign-up, ...), guessed from the URL path. Every result says so
(`basis: "heuristic"`, `measured: false`) and lists the reasons, so an owner can disagree with it.

No revenue, user-loss or traffic number is ever produced.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from app.diagnosis.traffic import TrafficIndex

SEVERITY_WEIGHT = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}


@dataclass(frozen=True)
class Journey:
    key: str
    label: str
    weight: int
    pattern: re.Pattern[str]


def _j(key: str, label: str, weight: int, words: str) -> Journey:
    return Journey(key, label, weight, re.compile(rf"(?:^|[/_.-])(?:{words})(?:$|[/_.-])", re.I))


# Ordered: the first match wins, so more critical journeys come first.
JOURNEYS: tuple[Journey, ...] = (
    _j(
        "checkout",
        "Checkout or payment",
        5,
        "checkout|cart|basket|payment|payments|pay|billing|order|orders|purchase|subscribe",
    ),
    _j("login", "Login", 4, "login|log-in|signin|sign-in|auth|sso|password|forgot|reset"),
    _j("signup", "Sign-up", 4, "signup|sign-up|register|registration|join|onboarding"),
    _j(
        "application",
        "Application or booking form",
        4,
        "apply|application|admission|admissions|exam|enroll|enrol|booking|book|reserve|appointment",
    ),
    _j("account", "Account area", 3, "account|profile|settings|dashboard|my"),
    _j("contact", "Contact or support", 3, "contact|support|help|enquiry|inquiry"),
    _j("search", "Search or catalogue", 2, "search|product|products|shop|store|catalog|catalogue"),
)
HOME = Journey("home", "Home page", 3, re.compile(r"^$"))
OTHER = Journey("other", "Other page", 1, re.compile(r"^$"))


def classify_path(url_or_path: str | None) -> Journey:
    if not url_or_path:
        return OTHER
    try:
        path = urlsplit(url_or_path).path
    except ValueError:
        return OTHER
    if path in ("", "/"):
        return HOME
    for journey in JOURNEYS:
        if journey.pattern.search(path):
            return journey
    return OTHER


@dataclass(frozen=True)
class Impact:
    score: int
    journey_key: str
    journey_label: str
    page: str | None
    reasons: list[str]
    basis: Literal["heuristic", "heuristic+customer_traffic"] = "heuristic"
    measured: bool = False
    traffic: dict[str, object] | None = None


def rank_issue(
    severity: str, page_urls: list[str], traffic: "TrafficIndex | None" = None
) -> Impact:
    """Scores one finding. `page_urls` are the pages its evidence was observed on.

    With customer-supplied traffic the score is weighted by the page's share of the imported views.
    That share is the owner's own number: it is labeled as such and never turned into money.
    """
    sev = SEVERITY_WEIGHT.get(severity, 1)
    best: tuple[Journey, str | None] = (OTHER, None)
    for url in page_urls:
        journey = classify_path(url)
        if journey.weight > best[0].weight or best[1] is None and journey.weight >= best[0].weight:
            best = (journey, url)
    journey, page = best
    reasons = [f"Severity is {severity} (weight {sev} of 5)."]
    if page is None:
        reasons.append(
            "No page was recorded for this finding, so it is treated as an ordinary page "
            "(weight 1)."
        )
    else:
        reasons.append(
            f"The affected page looks like: {journey.label.lower()} (weight {journey.weight} "
            "of 5), judged only from its address."
        )
    score = float(sev * journey.weight)
    basis: Literal["heuristic", "heuristic+customer_traffic"] = "heuristic"
    traffic_info: dict[str, object] | None = None
    if traffic is not None:
        basis = "heuristic+customer_traffic"
        views, share = traffic.share(page) if page else (0, 0.0)
        factor = traffic.factor(share)
        score *= factor
        traffic_info = {
            "source": "customer_supplied",
            "source_label": traffic.source_label,
            "period": f"{traffic.period_start} to {traffic.period_end}",
            "views": views,
            "share_percent": round(share * 100, 2),
            "factor": round(factor, 2),
        }
        reasons.append(
            f"Your uploaded traffic ({traffic.source_label}, {traffic.period_start} to "
            f"{traffic.period_end}) shows {views:,} views for this page, "
            f"{share * 100:.1f}% of the imported total. This raises the score by x{factor:.2f}. "
            "KMR AI did not measure or verify these numbers."
            if page
            else "No page was recorded, so your uploaded traffic could not be applied."
        )
        reasons.append("Only page views are used. No revenue or conversion figure is shown.")
    else:
        reasons.append(
            "This is a labeled estimate, not a measurement. No traffic data is connected, so no "
            "impact figures (traffic, revenue, user loss) are shown."
        )
    return Impact(
        score=round(score),
        journey_key=journey.key,
        journey_label=journey.label,
        page=page,
        reasons=reasons,
        basis=basis,
        traffic=traffic_info,
    )
