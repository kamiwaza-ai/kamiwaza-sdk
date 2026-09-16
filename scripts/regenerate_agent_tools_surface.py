#!/usr/bin/env python3
"""Regenerate the recorded agent-tools surface.

The surface an agent can reach is derived from this client's own methods, so it
changes whenever a method is added, renamed, or removed — including by accident.
This script writes what the surface currently is; CI regenerates it and fails
when the result differs from what is committed.

That turns an invisible change into a reviewable diff. A renamed method is a
breaking change for every agent host, because the published identifier derives
from the selector, and nothing else in the build would have said so.

Usage:
    python scripts/regenerate_agent_tools_surface.py [--check]

With ``--check`` the file is not written; the script exits non-zero when the
recorded surface is stale. Without it the file is rewritten in place.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

SURFACE_PATH = (
    Path(__file__).resolve().parents[1]
    / "kamiwaza_sdk"
    / "agent_tools"
    / "SURFACE.json"
)


def build_surface() -> dict[str, Any]:
    """Return the current surface as recordable data.

    Returns:
        Mapping with the index digest, the counts every gate asserts against,
        and the full selector list. The selector list is the point: a count
        alone says "something changed", while the list says what.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from kamiwaza_sdk.agent_tools.catalog import build_catalog, categories
        from kamiwaza_sdk.agent_tools.descriptors import (
            description_coverage,
            unknown_verbs,
        )
        from kamiwaza_sdk.agent_tools.schemas import underivable
        from kamiwaza_sdk.agent_tools.spec_index import build_index
        from kamiwaza_sdk.agent_tools.workflows import WORKFLOWS
        from kamiwaza_sdk.client import KamiwazaClient

        client = KamiwazaClient(base_url="http://localhost:7777/api")

    index = build_index(client)
    catalog = build_catalog(index, client)
    return {
        "descriptor_version": index.descriptor_version,
        "source_digest": index.source_digest,
        "counts": index.coverage(),
        "categories": categories(catalog),
        "descriptions": description_coverage(index, client),
        "workflows": sorted(WORKFLOWS),
        "unknown_verbs": list(unknown_verbs(index)),
        "underivable_schemas": list(underivable(index, client)),
        "withheld": sorted(
            entry.selector for entry in index if not entry.is_published
        ),
        "selectors": sorted(entry.selector for entry in index),
    }


def main() -> int:
    """Write or check the recorded surface.

    Returns:
        Process exit status: ``0`` when the recorded surface is current or was
        rewritten, ``1`` when ``--check`` found it stale.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the recorded surface is stale, without writing",
    )
    arguments = parser.parse_args()

    surface = build_surface()
    rendered = json.dumps(surface, indent=2, sort_keys=True) + "\n"

    if not arguments.check:
        SURFACE_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {SURFACE_PATH.relative_to(Path.cwd())}")
        return 0

    if not SURFACE_PATH.exists():
        print(f"{SURFACE_PATH} does not exist; run without --check", file=sys.stderr)
        return 1
    if SURFACE_PATH.read_text(encoding="utf-8") != rendered:
        print(
            "The recorded agent-tools surface is stale. Run "
            "`python scripts/regenerate_agent_tools_surface.py` and commit the "
            "result, checking the diff for a renamed or removed operation — a "
            "rename is a breaking change for every agent host.",
            file=sys.stderr,
        )
        return 1
    print("recorded surface is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
