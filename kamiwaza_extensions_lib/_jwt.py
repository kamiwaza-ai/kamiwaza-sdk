"""Signature-less JWT payload decoder shared across the runtime lib.

The platform's ForwardAuth layer validates the bearer's signature
*before* it reaches the extension; the extension never has the signing
key and never makes access-control decisions on the token's signature.
We only ever read claims (``sub``, ``email``, ``exp`` etc) for display,
expiry countdowns, and the local-dev-auth bridge's envelope synthesis.

This module is the single source of truth so ``session.py`` and
``local_dev.py`` cannot drift on segment-count strictness or padding
logic again (PR #87 round-10 review caught the prior divergence —
``session._decode_jwt_exp`` accepted 2-segment tokens while
``local_dev._decode_jwt_claims`` required 3, and the docstring claimed
they "mirror"). The 3-segment requirement is canonical for JWTs per
RFC 7519 and matches the TypeScript bridge in
``kamiwaza-ai-extensions-lib/src/local-dev-auth/index.ts``.
"""

from __future__ import annotations

import base64
import json


def decode_jwt_payload(token: str) -> dict:
    """Decode a JWT payload **without** signature verification.

    Returns ``{}`` on any decode failure (malformed segments, padding,
    base64 errors, non-UTF-8 bytes, non-JSON payload, non-object root).
    Requires the canonical three-segment shape — a 2-segment input is
    treated as malformed.
    """
    parts = token.split(".")
    if len(parts) < 3:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload}{padding}")
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def decode_jwt_exp(token: str) -> int | None:
    """Extract the ``exp`` claim from a JWT, or ``None`` if absent/malformed.

    Wrapper around :func:`decode_jwt_payload` that coerces the ``exp``
    value to ``int`` per RFC 7519 (NumericDate). Accepts string-form
    integers (some IdPs emit them) but rejects floats with fractional
    parts and any non-numeric value.
    """
    claims = decode_jwt_payload(token)
    exp = claims.get("exp")
    if isinstance(exp, bool):
        return None
    if isinstance(exp, (int, float)):
        return int(exp)
    if isinstance(exp, str) and exp.isdigit():
        return int(exp)
    return None
