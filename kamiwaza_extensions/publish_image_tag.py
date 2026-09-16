"""Derive Docker tags without changing an extension's SemVer identity."""

from __future__ import annotations

import hashlib
import json
from typing import Optional


def publish_image_tag(version: str, stage: str, revision: Optional[str] = None) -> str:
    """Keep ordinary tags; hash build metadata or oversized derived tags.

    The reserved underscore prefix cannot collide with a normal SemVer tag.
    A full SHA-256 of the version and stage preserves distinct build identities
    without truncation or delimiter ambiguity. Explicit revisions have already
    been validated by the CLI and retain their existing meaning. Catalog version
    identity is never replaced with this artifact tag.
    """
    if revision is not None:
        return revision
    raw_tag = version if stage == "prod" else f"{version}-{stage}"
    if "+" not in version and len(raw_tag) <= 128:
        return raw_tag
    identity = json.dumps([version, stage], separators=(",", ":"))
    return "_semver_sha256_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
