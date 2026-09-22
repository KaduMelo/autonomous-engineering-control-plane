"""HMAC verification of a webhook body.

The signature covers the **raw bytes** of the request. Anything that decodes,
normalizes or re-serializes the body before this runs would be verifying
something the sender did not sign.
"""

from __future__ import annotations

import hashlib
import hmac

PREFIX = "sha256="


def sign(body: bytes, secret: bytes) -> str:
    """The header value a sender would send for this body."""
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return f"{PREFIX}{digest}"


def verify(body: bytes, header: str | None, secret: bytes) -> bool:
    """True when `header` is a valid signature of `body`.

    Fails closed on an empty secret: an unset secret must reject everything, not
    accept anything that can compute an HMAC of the empty key.
    """
    if not secret or not header or not header.startswith(PREFIX):
        return False
    expected = sign(body, secret)
    # compare_digest, never ==: an early-exit comparison leaks how much of the
    # digest matched, one byte at a time.
    return hmac.compare_digest(expected, header)
