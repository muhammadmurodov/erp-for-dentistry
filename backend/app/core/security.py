"""Password hashing and bearer-token issuance.

Access tokens are short-lived, signed JWTs carrying the membership the caller
selected. Refresh tokens are opaque random strings; only a SHA-256 digest is
stored, so a database disclosure does not yield usable sessions.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.errors import AuthenticationFailed, ValidationFailed

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]

# Parameters follow the OWASP Password Storage Cheat Sheet second-choice
# Argon2id profile (19 MiB, 2 iterations, 1 lane).
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)

#: Rejected outright regardless of length. A short, documented list; the real
#: defence is length plus rate limiting, not a dictionary.
COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password123",
        "123456789012",
        "qwertyuiop12",
        "administrator",
        "letmein12345",
    }
)


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def validate_password_strength(password: str) -> None:
    settings = get_settings()
    problems: list[str] = []
    if len(password) < settings.password_min_length:
        problems.append(f"must be at least {settings.password_min_length} characters")
    if password.lower() in COMMON_PASSWORDS:
        problems.append("is too common")
    if password.strip() != password:
        problems.append("must not start or end with whitespace")
    if problems:
        raise ValidationFailed(
            "Password " + " and ".join(problems),
            details={"field": "password", "problems": problems},
        )


def create_access_token(
    *,
    user_id: uuid.UUID,
    email: str,
    organization_id: uuid.UUID | None,
    membership_id: uuid.UUID | None,
    permissions: frozenset[str] | set[str],
    is_superuser: bool = False,
) -> tuple[str, datetime]:
    """Sign an access token.

    Permissions are embedded so that the common case needs no database read,
    at the cost of staying valid until expiry after a revocation. The access
    TTL is deliberately short (15 minutes by default) to bound that window.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "eml": email,
        "typ": "access",
        "org": str(organization_id) if organization_id else None,
        "mem": str(membership_id) if membership_id else None,
        "prm": sorted(permissions),
        "sup": is_superuser,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    return jwt.encode(claims, settings.secret_key, algorithm=ALGORITHM), expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise AuthenticationFailed("Access token is invalid or has expired") from exc
    if claims.get("typ") != "access":
        raise AuthenticationFailed("Wrong token type")
    return claims


def generate_refresh_token() -> tuple[str, str]:
    """Return ``(plaintext, digest)``. Only the digest is ever stored."""
    plaintext = secrets.token_urlsafe(48)
    return plaintext, hash_refresh_token(plaintext)


def hash_refresh_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
