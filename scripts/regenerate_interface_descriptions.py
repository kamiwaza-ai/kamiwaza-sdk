#!/usr/bin/env python3
"""Regenerate the shipped interface descriptions from the vendored spec.

The interface document — ``kamiwaza-openapi-spec.json`` at the repository root
— is a **development artifact**: it is the platform's own document, vendored so
the drift gate has something to compare the client against. At runtime the
agent-tools layer needs one thing from it: each operation's ``summary`` and
``description``, so a published tool carries the platform's own wording rather
than a docstring paraphrase.

Those are two different jobs, and this script is the seam between them. It
extracts what the runtime reads into package data and leaves the rest out of
the wheel.

Measured at the time of writing: the full document is 278 KiB and the fields
the runtime reads are 41 KiB of it, so shipping the whole thing put 85% dead
weight in every install — and made a vendored snapshot of someone else's
document look like part of this package's API surface, which a consumer could
then read and mistake for the platform's current truth.

Paths are normalised here rather than at import: the shape
(``/models/{}``) is what the client's own call sites can be compared against,
and doing it once at generation removes the work from every process that
imports the layer.

Usage:
    python scripts/regenerate_interface_descriptions.py [--check]

With ``--check`` nothing is written; the script exits non-zero when the
generated file is stale, which is what CI runs. Without it the file is
rewritten in place.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

#: The vendored platform document. At the repository root, not inside the
#: package: it is gated, not shipped.
SPEC_PATH = Path(__file__).resolve().parents[1] / "kamiwaza-openapi-spec.json"

#: The generated package data the runtime reads. Upper-case like
#: ``SURFACE.json``, which is this repository's mark for "generated, do not
#: hand-edit".
DESCRIPTIONS_PATH = (
    Path(__file__).resolve().parents[1]
    / "kamiwaza_sdk"
    / "agent_tools"
    / "INTERFACE_DESCRIPTIONS.json"
)

_PATH_PARAMETER = re.compile(r"\{[^}]*\}")


def _normalise_path(path: str) -> str:
    """Return a path with every parameter segment reduced to a single token.

    The client interpolates its own parameter names (``{model_id}``) while the
    document uses the route's (``{model_uuid}``), so the names cannot be
    compared. The shape can.

    Args:
        path: Route path, possibly containing parameter placeholders.

    Returns:
        The path with each placeholder replaced by ``{}`` and any trailing
        slash removed, so ``/models/`` and ``/models`` compare equal.
    """
    flattened = _PATH_PARAMETER.sub("{}", path)
    return flattened.rstrip("/") or "/"


def build_descriptions() -> dict[str, dict[str, str]]:
    """Extract the runtime-visible fields from the vendored document.

    Returns:
        Mapping from ``"<METHOD> <normalised path>"`` to the operation's
        ``description`` and ``summary``, each present only when the document
        states it. A flat string key rather than a pair, because JSON has no
        tuple: the runtime splits it once on load.

    Raises:
        FileNotFoundError: The vendored document is absent, which means the
            drift gate has nothing to compare against either — a louder
            failure than silently generating an empty table.
    """
    document: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    descriptions: dict[str, dict[str, str]] = {}
    for path, operations in document.get("paths", {}).items():
        if not isinstance(operations, dict):
            continue
        for http_method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            stated = {
                field: operation[field]
                for field in ("description", "summary")
                if isinstance(operation.get(field), str) and operation[field].strip()
            }
            if stated:
                descriptions[f"{http_method.upper()} {_normalise_path(path)}"] = stated
    return descriptions


def main() -> int:
    """Write or check the generated descriptions.

    Returns:
        Process exit status: ``0`` when the file is current or was rewritten,
        ``1`` when ``--check`` found it stale.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the generated file is stale, without writing",
    )
    arguments = parser.parse_args()

    rendered = json.dumps(build_descriptions(), indent=2, sort_keys=True) + "\n"

    if not arguments.check:
        DESCRIPTIONS_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {DESCRIPTIONS_PATH.relative_to(Path.cwd())}")
        return 0

    if not DESCRIPTIONS_PATH.exists():
        print(
            f"{DESCRIPTIONS_PATH} does not exist; run without --check",
            file=sys.stderr,
        )
        return 1
    if DESCRIPTIONS_PATH.read_text(encoding="utf-8") != rendered:
        print(
            "The generated interface descriptions are stale. Run "
            "`python scripts/regenerate_interface_descriptions.py` and commit "
            "the result. A diff here means the vendored platform document "
            "changed, so review it as a change to what every published tool "
            "says about itself.",
            file=sys.stderr,
        )
        return 1
    print("generated interface descriptions are current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
