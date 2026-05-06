import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone


PBKDF2_ITERATIONS = 240_000
RESET_TOKEN_TTL_MINUTES = 30
ACTIVATION_TOKEN_TTL_HOURS = 72


def generate_salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return key.hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    candidate = hash_password(password=password, salt=salt)
    return hmac.compare_digest(candidate, password_hash)


def generate_reset_token() -> str:
    return secrets.token_urlsafe(48)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def reset_token_expiry() -> datetime:
    return now_utc() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)


def activation_token_expiry() -> datetime:
    return now_utc() + timedelta(hours=ACTIVATION_TOKEN_TTL_HOURS)
