"""What the agent never reads or does. The server enforces the same rules (backend/app/desktop/policy.py);
a test compares the two lists. These are not settings: a permission cannot switch them off.
Adding to the block list is allowed (`extra_apps`), removing from it is not."""

import re
from typing import Literal

SENSITIVE_APPS = (
    "keepass", "bitwarden", "1password", "lastpass", "dashlane", "keeper", "enpass", "seahorse",
    "keyring", "authenticator", "authy", "yubico", "signal", "whatsapp", "telegram", "thunderbird",
    "evolution", "outlook", "mail", "gmail", "proton", "discord", "slack", "teams", "bank", "wallet",
    "paypal", "phonepe", "paytm", "gpay", "metamask",
)  # fmt: skip
SENSITIVE_TITLE = (
    r"log ?in", r"sign ?in", r"password", r"passcode", r"\botp\b", r"one-?time", r"verification code",
    r"2fa", r"two-?factor", r"\bbank", r"net ?banking", r"\bupi\b", r"wallet", r"payment", r"checkout",
    r"card number", r"\bcvv\b", r"statement", r"tax return", r"aadhaar", r"passport", r"\binbox\b",
    r"compose", r"private",
)  # fmt: skip
_APP_RE = re.compile("|".join(re.escape(a) for a in SENSITIVE_APPS), re.IGNORECASE)
_TITLE_RE = re.compile("|".join(SENSITIVE_TITLE), re.IGNORECASE)

CONSEQUENTIAL = re.compile(
    r"\b(pay|payment|buy|purchase|place order|order now|checkout|confirm|submit|send|post|publish|"
    r"delete|remove|erase|transfer|withdraw|sign out|log ?out|unsubscribe|accept|agree|authori[sz]e|"
    r"approve|apply|book now|donate|subscribe|reply|forward|install|uninstall|format|reset|empty|"
    r"close|quit|exit|shut ?down|restart|power off)\b",
    re.IGNORECASE,
)
SECRET_FIELD = re.compile(
    r"(password|passcode|\bpin\b|\botp\b|one-?time|\bcvv\b|\bcvc\b|card ?number|security code|"
    r"expiry|aadhaar|aadhar|\bpan\b|\bssn\b|secret|token|api ?key|account ?number|iban|ifsc|"
    r"recovery|seed phrase|private key)",
    re.IGNORECASE,
)
PRESS_ROLES = ("button", "link", "menu_item", "tab")
TYPE_ROLES = ("text_field", "search_box", "text_area")
StepKind = Literal["highlight", "press", "type"]


def sensitive_window(app: str, title: str, extra_apps: tuple[str, ...] = ()) -> str | None:
    """Why a window must not be read, or None."""
    if _APP_RE.search(app or ""):
        return "this kind of app (passwords, mail, messages, banking) is never read"
    if any(word.lower() in (app or "").lower() for word in extra_apps if word):
        return "this app is on your own block list"
    if _TITLE_RE.search(title or ""):
        return "the window looks like a sign-in, payment or private message"
    return None


def step_refusal(
    kind: str, role: str, name: str, actionable: bool = True, password: bool = False
) -> str | None:
    """Why an action on this element must not be done, or None. `highlight` is always fine."""
    if kind == "highlight":
        return None
    if kind == "press":
        if role not in PRESS_ROLES or not actionable:
            return "this element cannot be pressed"
        if CONSEQUENTIAL.search(name or ""):
            return "pressing this could not be undone or makes a commitment, so you press it yourself"
        return None
    if kind == "type":
        if role not in TYPE_ROLES or not actionable:
            return "this element does not take typed text"
        if password or SECRET_FIELD.search(name or ""):
            return "the agent never types into fields for secrets or identifiers"
        return None
    return "unknown kind of step"
