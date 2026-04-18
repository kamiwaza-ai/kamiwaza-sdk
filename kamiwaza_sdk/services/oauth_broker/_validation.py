"""Shared input-validation helpers for the OAuth Broker service."""

from __future__ import annotations

import re

# Google Drive file IDs and tool identifiers use URL-safe base64-ish
# characters: letters, digits, hyphens, underscores, and dots.  Anything
# else is rejected upfront to prevent path-traversal / query-string
# injection.
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+\Z")


def _validate_safe_id(value: str, label: str) -> None:
    """Validate that *value* contains only URL-safe identifier characters.

    Raises ``ValueError`` when *value* is empty, contains characters
    outside ``[a-zA-Z0-9._-]``, or equals ``..`` (path traversal).
    This is used as a whitelist gate for identifiers (``tool_id``,
    ``file_id``, ``lease_id``) that are interpolated into URLs or
    query strings.
    """
    if not value or not _SAFE_ID_RE.match(value) or value == "..":
        raise ValueError(
            f"{label} contains characters that are not permitted "
            f"(must match [a-zA-Z0-9._-]+)"
        )
