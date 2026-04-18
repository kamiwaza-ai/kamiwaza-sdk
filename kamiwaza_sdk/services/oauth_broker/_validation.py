"""Shared input-validation helpers for the OAuth Broker service."""

from __future__ import annotations

import re

# Identifiers (file_id, lease_id) that are interpolated into URL *paths*
# may contain letters, digits, hyphens, underscores, dots, colons,
# forward slashes, and base64 characters (``=`` and ``+``).  The ``=``
# and ``+`` are needed because the broker may emit opaque base64-encoded
# lease IDs.  Values are always percent-encoded with ``quote(…, safe="")``
# before interpolation, so these extra characters are safe.
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9._:/%+=\-]+\Z")

# Control characters (including null, newline, carriage return) that must
# never appear in any identifier, even one passed as a query parameter.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _validate_safe_id(value: str, label: str) -> None:
    """Validate that *value* contains only safe identifier characters.

    Raises ``ValueError`` when *value* is empty, contains characters
    outside ``[a-zA-Z0-9._:/%+=\\-]``, or contains ``..`` (path traversal).
    This is used as a whitelist gate for identifiers (``file_id``,
    ``lease_id``) that are interpolated into URL paths.
    """
    if not value or not _SAFE_ID_RE.match(value) or ".." in value:
        raise ValueError(
            f"{label} contains characters that are not permitted "
            f"(must match [a-zA-Z0-9._:/%+=\\-]+)"
        )


def _validate_query_param_id(value: str, label: str) -> None:
    """Validate an identifier that will be sent as a query parameter.

    This is intentionally more permissive than :func:`_validate_safe_id`
    because query-parameter values are automatically URL-encoded by the
    ``requests`` library and never interpolated into URL paths.  The
    server-side schema accepts arbitrary strings for fields such as
    ``tool_id`` (e.g. ``"Gmail Reader"``), so we only reject values
    that are empty, contain control characters, or contain ``..``
    path-traversal sequences.
    """
    if not value or _CONTROL_CHAR_RE.search(value) or ".." in value:
        raise ValueError(
            f"{label} must be a non-empty string without control "
            f"characters or path-traversal sequences"
        )
