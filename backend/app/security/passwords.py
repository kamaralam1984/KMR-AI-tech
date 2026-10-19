import anyio.to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# argon2-cffi's defaults are the RFC 9106 low-memory profile (Argon2id).
_hasher = PasswordHasher()
# Verified when the account does not exist, so response time does not reveal whether it does.
_DUMMY_HASH = _hasher.hash("not-a-real-password-used-for-timing")

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


async def hash_password(password: str) -> str:
    # Hashing is deliberately slow and CPU-bound, so it runs off the event loop.
    return await anyio.to_thread.run_sync(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    def check() -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    return await anyio.to_thread.run_sync(check)


async def verify_dummy(password: str) -> None:
    await verify_password(_DUMMY_HASH, password)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
