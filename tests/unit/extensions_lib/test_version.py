"""Pin the runtime-lib version. ENG-3892 / D210 M2 / Task T2.9."""

from __future__ import annotations

import re
from pathlib import Path

import kamiwaza_extensions_lib

REPO_ROOT = Path(__file__).resolve().parents[3]
CHANGELOG_PATH = REPO_ROOT / "kamiwaza_extensions_lib" / "CHANGELOG.md"


def test_version_is_0_3_0_for_m2():
    assert kamiwaza_extensions_lib.__version__ == "0.3.0", (
        "M2 ships the runtime lib as 0.3.0 (signals cross-language parity "
        "against test-vectors.json). Update both __version__ and CHANGELOG.md "
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
