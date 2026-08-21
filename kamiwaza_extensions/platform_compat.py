"""Platform compatibility constants for the kz-ext CLI.

Records the ``extension-operator`` image + tag set that this CLI version is
known-compatible with. Makes the previously-implicit SDK → operator contract
explicit.

Design reference: §4.2.16 ``OperatorImagePin``.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlparse

OPERATOR_IMAGE = "ghcr.io/kamiwaza-internal/operators/images/extension-operator"

# The extension CLI is released in lockstep with the platform/operator. This
# is the tested compatibility contract, not a claim that older tags cannot work.
OPERATOR_COMPATIBLE_TAGS: Tuple[str, ...] = (
    "release-1.0.0",
)

OPERATOR_NAMESPACE = "kamiwaza-system"
OPERATOR_DEPLOYMENT = "extension-operator"
EXTENSION_CRD = "kamiwazaextensions.extensions.kamiwaza.io"

_RELEASE_TAG_RE = re.compile(r"^release-\d+\.\d+\.\d+$")


def parse_image_ref(image_ref: str) -> Tuple[str, Optional[str]]:
    """Split an image reference into ``(repository, tag)``.

    Handles registries with port numbers like ``ghcr.io/x/y:tag`` and
    digests like ``ghcr.io/x/y@sha256:...`` (digest returned in the tag
    slot, prefixed with ``sha256:`` so callers can distinguish it from a
    release tag — see :func:`is_digest_ref`).
    """
    if "@" in image_ref:
        repo, _, digest = image_ref.partition("@")
        return repo, digest
    slash = image_ref.rfind("/")
    after_slash = image_ref[slash + 1 :] if slash >= 0 else image_ref
    if ":" in after_slash:
        name, _, tag = after_slash.rpartition(":")
        prefix = image_ref[: slash + 1] if slash >= 0 else ""
        return f"{prefix}{name}", tag
    return image_ref, None


def is_digest_ref(tag: Optional[str]) -> bool:
    """Return ``True`` if ``tag`` is a digest (e.g. ``sha256:abc123``).

    ``parse_image_ref`` returns the digest in the tag slot for refs like
    ``ghcr.io/x/y@sha256:...``. A digest is opaque from the tag-name
    perspective — we can't infer release-version compatibility from it
    without a registry round-trip — so callers should skip
    :func:`is_compatible_tag` checks on digest refs (review re-review
    PR #84 H1: digest-pinned operator deploys were silently
    misclassified as incompatible).
    """
    return tag is not None and tag.startswith("sha256:")


def is_compatible_tag(tag: Optional[str]) -> bool:
    """Return ``True`` if ``tag`` is in ``OPERATOR_COMPATIBLE_TAGS``.

    Note: this returns ``False`` for digest refs (``sha256:...``) even
    though the underlying image may be perfectly compatible — callers
    should gate on :func:`is_digest_ref` first and treat digest refs as
    opaque-but-trusted (skip the compat warning).
    """
    return tag is not None and tag in OPERATOR_COMPATIBLE_TAGS


def validate_compatible_tag_grammar(tag: str) -> bool:
    """Return ``True`` if ``tag`` matches the canonical ``release-X.Y.Z`` grammar.

    Used by the CI sanity-check to catch typos in ``OPERATOR_COMPATIBLE_TAGS``
    before the GHCR resolve step runs.
    """
    return bool(_RELEASE_TAG_RE.match(tag))


# Hostnames where it's safe to assume the local kubectl context targets the
# same cluster as the Kamiwaza HTTP connection — i.e., the user is running
# a local development cluster. For remote SaaS connections, the kube-context
# is by definition unrelated to the Kamiwaza cluster, so kubectl-based
# probes (cluster_extension_readiness, dev timeout diagnostics) would
# inspect the wrong cluster and emit confidently-wrong guidance.
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_LOCAL_TLD_SUFFIXES = (".test", ".local", ".localhost")


def is_local_connection(url: Optional[str]) -> bool:
    """Return ``True`` if ``url`` looks like a local-dev Kamiwaza endpoint.

    Used to gate kubectl-based probes: only when the connection points at
    a localhost or local-development URL can we assume the local kubectl context
    targets the same cluster (review PR #84 H1/H2). Remote connections
    (`https://kamiwaza.cloud/api`, customer SaaS endpoints) intentionally
    skip the probes — there is no way to verify the kube-context matches
    the HTTP connection without a richer ``ConnectionInfo`` schema, which
    is out of scope for this fix.

    Detection is deliberately permissive (TLD-based) so the local-development
    convention `https://kamiwaza.test/api` keeps working. Operators with
    other local-dev domains can extend ``_LOCAL_TLD_SUFFIXES``.
    """
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    return host.endswith(_LOCAL_TLD_SUFFIXES)
