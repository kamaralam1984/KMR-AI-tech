import hashlib
import hmac
import secrets

COOKIE_NAME_SECURE = "__Host-kmr_session"  # the __Host- prefix requires Secure and Path=/
COOKIE_NAME_PLAIN = "kmr_session"  # local http development only
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def cookie_name(secure: bool) -> str:
    return COOKIE_NAME_SECURE if secure else COOKIE_NAME_PLAIN


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """Only this digest is stored, so a database leak does not yield usable session tokens."""
    return hashlib.sha256(token.encode()).digest()


def csrf_token(secret_key: str, token_hash: bytes) -> str:
    """Derived from the session, so it needs no storage and dies with the session."""
    return hmac.new(secret_key.encode(), b"csrf:" + token_hash, hashlib.sha256).hexdigest()


def csrf_matches(secret_key: str, token_hash: bytes, supplied: str | None) -> bool:
    if not supplied:
        return False
    return hmac.compare_digest(csrf_token(secret_key, token_hash), supplied)
