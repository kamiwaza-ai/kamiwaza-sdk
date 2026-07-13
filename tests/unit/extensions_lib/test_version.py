"""Pin the runtime-lib version. ENG-3892 / D210 M2 / Task T2.9."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import kamiwaza_extensions_lib

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_DIR = REPO_ROOT / "kamiwaza_extensions_lib"
CHANGELOG_PATH = LIB_DIR / "CHANGELOG.md"
LIB_PYPROJECT_PATH = LIB_DIR / "pyproject.toml"


def test_version_is_0_4_3():
    # M3 / PR #87 round-9 promoted the round-8 ``_url`` helpers to a
    # public ``url`` module (and re-exported ``backend_runtime_base`` /
    # ``public_base_url`` from the package root). Scaffolded extensions
    # now import the public path, so the compat floor in
    # ``compatibility.json`` was raised to ``>=0.4,<0.5`` to keep older
    # versions without the helpers from resolving. 0.4.1 (ENG-6911)
    # fixed the session router's logout to proxy core's front-channel
    # logout URL; 0.4.2 (ENG-6911) corrected that fix to build the
    # front-channel URL from the browser base directly, since the
    # server-side proxy POST is unreachable in-cluster under ``kz-ext
    # dev`` — still within the ``>=0.4,<0.5`` compat range. 0.4.3
    # publishes the patched FastAPI/Starlette dependency floor for
    # standalone PyPI consumers while preserving the same compat range.
    assert kamiwaza_extensions_lib.__version__ == "0.4.3", (
        "Runtime lib is 0.4.3 (ENG-8614 security dependency floor). "
        "Update both __version__ and CHANGELOG.md if the version is "
        "intentionally changing."
    )


def test_changelog_documents_current_version():
    text = CHANGELOG_PATH.read_text()
    current = kamiwaza_extensions_lib.__version__
    pattern = rf"## \[{re.escape(current)}\]"
    assert re.search(pattern, text), (
        f"CHANGELOG.md must have a `## [{current}]` heading documenting the "
        f"current __version__. Add an entry before bumping __version__."
    )


def test_pyproject_version_matches_dunder_version():
    """The lib has two version sources of truth (pyproject + __init__.py).

    Bumping one without the other will silently desync — the wheel's
    METADATA reflects the pyproject version while runtime code keys off
    ``__version__``. This test wires them together so a partial bump
    fails CI.
    """
    with LIB_PYPROJECT_PATH.open("rb") as f:
        pyproject = tomllib.load(f)
    pyproject_version = pyproject["project"]["version"]
    assert pyproject_version == kamiwaza_extensions_lib.__version__, (
        f"kamiwaza_extensions_lib/pyproject.toml [project].version "
        f"({pyproject_version!r}) and kamiwaza_extensions_lib.__version__ "
        f"({kamiwaza_extensions_lib.__version__!r}) disagree. Bump both, "
        f"or only bump the source of truth and the other auto-derives."
    )
