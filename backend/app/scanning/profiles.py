"""Scan profiles: how the scanner's browser pretends to be a different device or network.

A profile only changes the *browser environment* (screen size, touch, CPU speed, network speed).
Every Phase 3 rule still applies on top of it: same host scope, same egress proxy, same page,
request, byte and time limits. Throttling is applied with Chrome DevTools commands, so it works for
Chromium only; asking for another engine is refused unless the browser service provides it.

Numbers measured under a profile (such as load time) are measured values *from the scan
environment under that throttling*. They are not what a real visitor on a real phone would see.
"""

from dataclasses import dataclass
from typing import Any

from playwright.async_api import BrowserContext, Page

DEFAULT_PROFILE = "desktop"
DEFAULT_ENGINE = "chromium"
ENGINES = ("chromium", "firefox", "webkit")


@dataclass(frozen=True)
class ScanProfile:
    key: str
    label: str
    description: str
    width: int
    height: int
    mobile: bool = False
    device_scale_factor: float = 1.0
    # Chrome's CPU throttling multiplier (1 = none, 4 = four times slower).
    cpu_slowdown: float = 1.0
    # Bytes per second and milliseconds; None means the network is not throttled.
    download_bps: int | None = None
    upload_bps: int | None = None
    latency_ms: int | None = None
    # How many scans of quota this profile uses: slower profiles occupy the browser longer.
    weight: int = 1
    # Slower pages need more time to load, but never beyond the overall scan timeout.
    page_timeout_factor: float = 1.0

    @property
    def throttled(self) -> bool:
        return self.cpu_slowdown > 1 or self.download_bps is not None


PROFILES: dict[str, ScanProfile] = {
    p.key: p
    for p in (
        ScanProfile("desktop", "Desktop", "1280 x 800 screen, no throttling.", 1280, 800),
        ScanProfile(
            "phone",
            "Small phone",
            "360 x 640 touch screen, no throttling.",
            360,
            640,
            mobile=True,
            device_scale_factor=2.0,
        ),
        ScanProfile(
            "slow_network",
            "Slow network (3G-like)",
            "Desktop screen on a throttled connection: about 1.6 Mbit/s down, 0.75 Mbit/s up, "
            "150 ms latency.",
            1280,
            800,
            download_bps=200_000,
            upload_bps=94_000,
            latency_ms=150,
            weight=2,
            page_timeout_factor=2.0,
        ),
        ScanProfile(
            "low_end_phone",
            "Low-end phone on 3G",
            "Small phone screen, CPU four times slower, and the 3G-like connection.",
            360,
            640,
            mobile=True,
            device_scale_factor=2.0,
            cpu_slowdown=4.0,
            download_bps=200_000,
            upload_bps=94_000,
            latency_ms=150,
            weight=3,
            page_timeout_factor=3.0,
        ),
    )
}


def get_profile(key: str | None) -> ScanProfile:
    profile = PROFILES.get(key or DEFAULT_PROFILE)
    if profile is None:
        raise KeyError(key)
    return profile


def engine_supports(profile: ScanProfile, engine: str) -> str | None:
    """Why this engine cannot run this profile (or None). Throttling needs Chrome DevTools."""
    if profile.throttled and engine != "chromium":
        return (
            f"The {profile.label} profile needs CPU and network throttling, which only the "
            "Chromium browser supports. Choose Chromium, or a profile without throttling."
        )
    return None


def context_options(profile: ScanProfile, engine: str = "chromium") -> dict[str, Any]:
    options: dict[str, Any] = {
        "viewport": {"width": profile.width, "height": profile.height},
        "device_scale_factor": profile.device_scale_factor,
    }
    if profile.mobile:
        options["has_touch"] = True
        if engine != "firefox":  # Firefox cannot emulate a mobile browser, only the screen size
            options["is_mobile"] = True
    return options


async def apply_throttling(
    context: BrowserContext, page: Page, profile: ScanProfile, engine: str = "chromium"
) -> None:
    """Applies CPU and network throttling through a DevTools session (Chromium only)."""
    if not profile.throttled:
        return
    if engine != "chromium":
        raise RuntimeError("Throttling is only available in Chromium.")
    session = await context.new_cdp_session(page)
    if profile.cpu_slowdown > 1:
        await session.send("Emulation.setCPUThrottlingRate", {"rate": profile.cpu_slowdown})
    if profile.download_bps is not None:
        await session.send("Network.enable")
        await session.send(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": profile.latency_ms or 0,
                "downloadThroughput": profile.download_bps,
                "uploadThroughput": profile.upload_bps or profile.download_bps,
            },
        )


def describe(profile: ScanProfile, engine: str = DEFAULT_ENGINE) -> dict[str, Any]:
    """What is stored with the scan so the same conditions can be repeated and reported."""
    return {
        "key": profile.key,
        "label": profile.label,
        "engine": engine,
        "viewport": [profile.width, profile.height],
        "mobile": profile.mobile,
        "cpu_slowdown": profile.cpu_slowdown,
        "download_kbit_s": None
        if profile.download_bps is None
        else round(profile.download_bps * 8 / 1000),
        "latency_ms": profile.latency_ms,
        "quota_weight": profile.weight,
    }
