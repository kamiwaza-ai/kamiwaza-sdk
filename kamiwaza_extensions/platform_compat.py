"""Platform compatibility constants for the kz-ext CLI.

Records the ``extension-operator`` image + tag set that this CLI version is
known-compatible with. Makes the previously-implicit SDK → operator contract
explicit.

Design reference: §4.2.16 ``OperatorImagePin``.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

OPERATOR_IMAGE = "ghcr.io/kamiwaza-internal/operators/images/extension-operator"

# Tags this CLI version is known-compatible with. Maintained as part of
# each SDK release cut; CI sanity-check confirms each tag resolves at GHCR.
OPERATOR_COMPATIBLE_TAGS: Tuple[str, ...] = (
    "release-0.12.1",
    "release-0.12.2",
)

OPERATOR_NAMESPACE = "kamiwaza-system"
OPERATOR_DEPLOYMENT = "extension-operator"
EXTENSION_CRD = "kamiwazaextensions.extensions.kamiwaza.io"

_RELEASE_TAG_RE = re.compile(r"^release-\d+\.\d+\.\d+$")


def parse_image_ref(image_ref: str) -> Tuple[str, Optional[str]]:
    """Split an image reference into ``(repository, tag)``.

    Handles registries with port numbers like ``ghcr.io/x/y:tag`` and
    digests like ``ghcr.io/x/y@sha256:...`` (digest returned as the tag).
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


def is_compatible_tag(tag: Optional[str]) -> bool:
    """Return ``True`` if ``tag`` is in ``OPERATOR_COMPATIBLE_TAGS``."""
    return tag is not None and tag in OPERATOR_COMPATIBLE_TAGS


def validate_compatible_tag_grammar(tag: str) -> bool:
    """Return ``True`` if ``tag`` matches the canonical ``release-X.Y.Z`` grammar.

    Used by the CI sanity-check to catch typos in ``OPERATOR_COMPATIBLE_TAGS``
    before the GHCR resolve step runs.
    """
    return bool(_RELEASE_TAG_RE.match(tag))
