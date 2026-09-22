"""Owner password hashes shared with the pre-container installer.

Keep this module standard-library-only and compatible with Python 3.6.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
from typing import Optional


_PBKDF2_ITERATIONS = 600_000
_PASSWORD_HASH_LENGTH = 32


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_owner_password(password: str, *, salt: Optional[bytes] = None) -> str:
    if len(password) < 12:
        raise ValueError("Owner password must contain at least 12 characters")
    if "\x00" in password or "\r" in password or "\n" in password:
        raise ValueError("Owner password contains unsupported control characters")
    resolved_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt,
        _PBKDF2_ITERATIONS,
        dklen=_PASSWORD_HASH_LENGTH,
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(_PBKDF2_ITERATIONS),
            _b64encode(resolved_salt),
            _b64encode(digest),
        )
    )


def verify_owner_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        if iterations != _PBKDF2_ITERATIONS:
            return False
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
        if len(salt) < 16 or len(expected) != _PASSWORD_HASH_LENGTH:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected),
        )
    except (binascii.Error, TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def owner_password_hash_is_valid(encoded_hash: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded_hash.split("$")
        return bool(
            algorithm == "pbkdf2_sha256"
            and int(raw_iterations) == _PBKDF2_ITERATIONS
            and len(_b64decode(raw_salt)) >= 16
            and len(_b64decode(raw_digest)) == _PASSWORD_HASH_LENGTH
        )
    except (binascii.Error, TypeError, ValueError):
        return False
