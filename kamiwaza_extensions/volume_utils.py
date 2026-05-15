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
    """Return True if *source* names a host-side path (not a named volume).

    A ``$`` anywhere in the source means shell/compose variable
    interpolation (``${PWD}/src``, ``$HOME/.cache``). Such a source
    resolves to a host path at runtime and can never be a valid named
    volume — compose volume names are restricted to ``[A-Za-z0-9._-]``.
    Treating it as a host path makes the validator reject it instead of
    the payload builder silently turning ``${PWD}/src`` into an
    ``emptyDir`` over the image's baked application files.
    """
    return (
        source.startswith(("/", "./", "../", "~"))
        or source in {".", ".."}
        or "$" in source
        or bool(_WINDOWS_DRIVE_RE.match(source))
    )
