"""Shared constants for kamiwaza-extensions."""

COMPOSE_FILENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

EXTENSIONS_NAMESPACE = "kamiwaza-extensions"


def extract_user_id(access_token: str) -> str:
    """Extract a stable user identifier (``sub`` claim) from a JWT.

    Falls back to hashing the token if decoding fails.
    """
    import base64
    import json as _json

    try:
        payload_b64 = access_token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        sub = payload.get("sub")
        if sub:
            return sub
    except Exception:
        pass
    import hashlib
    return hashlib.sha256(access_token.encode()).hexdigest()
