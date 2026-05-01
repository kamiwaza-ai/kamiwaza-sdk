"""Pin the runtime-lib version. ENG-3892 / D210 M2 / Task T2.9."""

from __future__ import annotations

import re
from pathlib import Path

import kamiwaza_extensions_lib

REPO_ROOT = Path(__file__).resolve().parents[3]
CHANGELOG_PATH = REPO_ROOT / "kamiwaza_extensions_lib" / "CHANGELOG.md"


def test_version_is_0_4_0_for_m3():
    # M3 / PR #87 round-9 promoted the round-8 ``_url`` helpers to a
    # public ``url`` module (and re-exported ``backend_runtime_base`` /
    # ``public_base_url`` from the package root). Scaffolded extensions
    # now import the public path, so the compat floor in
    # ``compatibility.json`` was raised to ``>=0.4,<0.5`` to keep older
    # versions without the helpers from resolving.
    assert kamiwaza_extensions_lib.__version__ == "0.4.0", (
        "M3 ships the runtime lib as 0.4.0 (public url helpers + "
        "local_dev bridge). Update both __version__ and CHANGELOG.md "
        "if the version is intentionally changing."
    )


def test_changelog_documents_current_version():
    text = CHANGELOG_PATH.read_text()
    current = kamiwaza_extensions_lib.__version__
    pattern = rf"## \[{re.escape(current)}\]"
    assert re.search(pattern, text), (
        f"CHANGELOG.md must have a `## [{current}]` heading documenting the "
        f"current __version__. Add an entry before bumping __version__."
    )
