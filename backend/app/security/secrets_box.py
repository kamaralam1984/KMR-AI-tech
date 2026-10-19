"""Encryption at rest for secrets customers give us (API-check credentials).

Fernet (AES-128-CBC with HMAC-SHA256, from the maintained `cryptography` package) is used instead
of anything hand-made. Ciphertext goes in the database; the key lives only in the environment.
Decrypted values exist only inside the worker or request that sends them, and are never logged,
stored elsewhere or returned by the API.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class SecretsUnavailable(Exception):  # noqa: N818 - domain term
    """No usable encryption key is configured, so secrets cannot be stored or read."""


def _fernet(settings: Settings) -> Fernet:
    if settings.encryption_key is None:
        raise SecretsUnavailable("KMR_ENCRYPTION_KEY is not set")
    try:
        return Fernet(settings.encryption_key.get_secret_value().encode())
    except ValueError as exc:  # malformed key
        raise SecretsUnavailable("KMR_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_secret(settings: Settings, value: str) -> bytes:
    return _fernet(settings).encrypt(value.encode())


def decrypt_secret(settings: Settings, ciphertext: bytes) -> str | None:
    """None if the ciphertext cannot be read (wrong or rotated key); callers treat it as unset."""
    try:
        return _fernet(settings).decrypt(ciphertext).decode()
    except InvalidToken:
        return None
