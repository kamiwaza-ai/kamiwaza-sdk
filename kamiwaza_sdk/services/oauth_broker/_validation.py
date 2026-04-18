"""Shared input-validation helpers for the OAuth Broker service."""

from __future__ import annotations

import re

# Identifiers (tool_id, file_id, lease_id) may contain letters, digits,
# hyphens, underscores, dots, colons, and forward slashes. Colons and
# slashes are needed for namespaced tool IDs (e.g. "google/gmail:reader").
# Anything else is rejected to prevent injection.
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9._:/-]+\Z")


def _validate_safe_id(value: str, label: str) -> None:
    """Validate that *value* contains only safe identifier characters.

    Raises ``ValueError`` when *value* is empty, contains characters
    outside ``[a-zA-Z0-9._:/-]``, or contains ``..`` (path traversal).
    This is used as a whitelist gate for identifiers (``tool_id``,
    ``file_id``, ``lease_id``) that are interpolated into URLs or
    query strings.
    """
    if not value or not _SAFE_ID_RE.match(value) or ".." in value:
        raise ValueError(
            f"{label} contains characters that are not permitted "
            f"(must match [a-zA-Z0-9._:/-]+)"
        )
