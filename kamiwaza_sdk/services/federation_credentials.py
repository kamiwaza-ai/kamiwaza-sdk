"""ENG-8213 S8 (design §7.5) — source-side per-target federation credential
resolution.

A source user targeting a ``receiver_realm`` federation obtains a per-receiver
credential OUT OF BAND (the receiver mints it at guest enrollment) and must
present it on mesh calls to that receiver — the receiver validates it against its
own ``federation-<id>`` realm, not the caller's local login. This module resolves
which credential to attach for which target; the source mesh proxy then forwards
it verbatim as ``X-KZ-Federation-Credential``.

Resolution precedence (both optional — the local ``KAMIWAZA_PAT`` still serves
local calls unchanged):

1. ``KAMIWAZA_FEDERATION_CREDENTIAL_<TARGET>`` env var, where ``<TARGET>`` is the
   federation name upper-cased with non-alphanumeric characters mapped to ``_``.
2. a JSON credential file at ``KAMIWAZA_FEDERATION_CREDENTIAL_FILE`` mapping the
   (verbatim) federation name to its credential.

Returns ``None`` when no per-target credential is configured — the caller then
makes an ordinary (local-identity) mesh call, unchanged from 1.0/1.1.
"""

from __future__ import annotations

import json
import os
import re
from typing import Mapping, Optional

# The on-wire header the source mesh proxy forwards to the receiver as the peer
# token for a receiver_realm target. Must match kamiwaza
# mesh.constants.MESH_HEADER_FEDERATION_CREDENTIAL.
FEDERATION_CREDENTIAL_HEADER = "X-KZ-Federation-Credential"

_ENV_PREFIX = "KAMIWAZA_FEDERATION_CREDENTIAL_"
_ENV_FILE = "KAMIWAZA_FEDERATION_CREDENTIAL_FILE"


def _env_suffix(target: str) -> str:
    """Map a federation name to its env-var suffix: upper-case, non-alphanumeric
    → ``_`` (so ``orion-prod`` → ``ORION_PROD``)."""
    return re.sub(r"[^A-Za-z0-9]", "_", target).upper()


def _load_credential_mapping(file_path: str) -> Optional[dict[str, object]]:
    """Load a credential mapping, returning ``None`` for unusable files."""
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            mapping = json.load(fh)
    except (OSError, ValueError):
        return None
    return mapping if isinstance(mapping, dict) else None


def resolve_federation_credential(
    target: str, *, env: Optional[Mapping[str, str]] = None
) -> Optional[str]:
    """Resolve the per-target receiver-issued federation credential, or ``None``.

    Args:
        target: the federation name (as used for ``client.federations[name]``).
        env: environment mapping to read (defaults to ``os.environ``); injectable
            for tests.
    """
    environ: Mapping[str, str] = os.environ if env is None else env

    from_env = environ.get(_ENV_PREFIX + _env_suffix(target))
    if from_env:
        return from_env

    file_path = environ.get(_ENV_FILE)
    if not file_path:
        return None

    mapping = _load_credential_mapping(file_path)
    if mapping is None:
        return None

    value = mapping.get(target)
    return value if isinstance(value, str) and value else None


def federation_credential_headers(
    target: str, *, env: Optional[Mapping[str, str]] = None
) -> dict[str, str]:
    """Return the ``X-KZ-Federation-Credential`` header for ``target`` when a
    per-target credential is configured, else an empty dict (attach verbatim to a
    mesh request; no-op for non-receiver_realm targets)."""
    credential = resolve_federation_credential(target, env=env)
    return {FEDERATION_CREDENTIAL_HEADER: credential} if credential else {}
