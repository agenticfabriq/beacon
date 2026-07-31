"""API-key generation, hashing, and verification.

Keys are high entropy, so storage uses SHA-256 plus constant-time comparison
rather than a slow password KDF.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_api_key(*, prefix: str = "bcn_dev") -> str:
    """Return a new high-entropy API key with the given prefix."""
    random_part = secrets.token_urlsafe(32)
    return f"{prefix}_{random_part}"


def hash_api_key(key: str) -> str:
    """Return the SHA-256 hex digest used to store an API key at rest."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def verify_api_key(key: str, expected_hash: str) -> bool:
    """Constant-time compare a raw API key against its stored hash."""
    return hmac.compare_digest(hash_api_key(key), expected_hash)
