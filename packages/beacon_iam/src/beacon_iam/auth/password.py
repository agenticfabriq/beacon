"""Password hashing and verification helpers."""

from __future__ import annotations

import bcrypt


def hash_password(password: str, *, cost: int = 12) -> str:
    """Return a bcrypt hash of the password at the given work factor."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(cost)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Return True when the password matches the stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (TypeError, ValueError):
        return False
