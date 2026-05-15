"""Shared classifiers for compose volume entries.

Both ``payload_builder`` and ``validators.compose`` need to recognise host
paths so they can either translate or reject them. Keeping the rules in
one module avoids the validator and payload builder drifting on what
counts as a bind mount.
"""

from __future__ import annotations

import re

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def looks_like_host_path(source: str) -> bool:
    """Return True if *source* names a host-side path (not a named volume)."""
    return (
        source.startswith(("/", "./", "../", "~"))
        or source in {".", ".."}
        or bool(_WINDOWS_DRIVE_RE.match(source))
    )
