"""Turning a project's site settings into API output, and user input into a validated target."""

from fastapi import HTTPException

from app.config import Settings
from app.db.models import Project
from app.scanning.net_policy import NetPolicy, Target, TargetRejected, validate_url
from app.scanning.ownership import dns_record_name, file_url, proof_value
from app.schemas import SiteOut, VerificationOut

_MESSAGES = {
    "unsupported_scheme": "Only http:// and https:// addresses can be scanned.",
    "credentials_in_url": "Remove the username and password from the address.",
    "blocked_host": "That address points to a private or internal host, which cannot be scanned.",
    "blocked_address": "That address points to a private network, which cannot be scanned.",
    "blocked_port": "Only the standard web ports (80 and 443) are supported.",
    "invalid_host": "That is not a valid website address.",
    "invalid_url": "That is not a valid website address.",
}


def net_policy(settings: Settings) -> NetPolicy:
    return NetPolicy(
        allowed_ports=tuple(settings.scan_allowed_ports),
        allowed_networks=("127.0.0.0/8",) if settings.scan_allow_private_targets else (),
    )


def parse_site_url(site_url: str, settings: Settings) -> Target:
    try:
        return validate_url(site_url.strip(), net_policy(settings))
    except TargetRejected as rejected:
        message = _MESSAGES.get(rejected.code, "That address cannot be scanned.")
        raise HTTPException(422, message) from rejected


def describe_site(project: Project) -> SiteOut:
    host = project.site_host
    scheme = "https"
    if project.site_url and project.site_url.startswith("http://"):
        scheme = "http"
    verified = project.is_verified
    return SiteOut(
        site_url=project.site_url,
        site_host=host,
        inject_request_id=project.inject_request_id,
        exclusions=list(project.scan_exclusions or []),
        max_pages=project.scan_max_pages,
        verification=VerificationOut(
            verified=verified,
            method=project.verified_method if verified else None,
            verified_at=project.verified_at if verified else None,
            dns_record_name=dns_record_name(host) if host else None,
            dns_record_value=proof_value(project.verification_token) if host else None,
            file_url=file_url(scheme, host) if host else None,
            file_content=proof_value(project.verification_token) if host else None,
        ),
    )
