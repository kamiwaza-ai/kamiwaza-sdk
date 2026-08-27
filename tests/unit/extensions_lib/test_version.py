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
SDK_PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
RELEASE_SCRIPT_PATH = REPO_ROOT / "release.sh"


def test_version_is_0_5_0():
    # Runtime 0.5 adds the canonical path-routing helpers and ASGI launcher
    # required by the dual-artifact Next.js scaffold. The compatibility
    # floor moves with it so every freshly generated app has both sides of
    # the runtime-relocation contract.
    assert kamiwaza_extensions_lib.__version__ == "0.5.0", (
        "Runtime lib is 0.5.0 (dual-artifact path relocation contract). "
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


def test_sdk_dependency_requires_current_runtime_release():
    with SDK_PYPROJECT_PATH.open("rb") as f:
        pyproject = tomllib.load(f)

    assert (
        "kamiwaza-extensions-lib>=0.5,<0.6" in pyproject["project"]["dependencies"]
    ), (
        "kamiwaza-sdk must require runtime-lib 0.5 so a fresh install "
        "cannot resolve a version without the path-relocation contract"
    )


def test_asgi_launcher_declares_uvicorn_dependency():
    with LIB_PYPROJECT_PATH.open("rb") as f:
        pyproject = tomllib.load(f)

    assert any(
        dependency.startswith("uvicorn>=")
        for dependency in pyproject["project"]["dependencies"]
    ), "python -m kamiwaza_extensions_lib.asgi requires a declared uvicorn runtime"


def test_release_publishes_required_npm_runtime_before_sdk():
    script = RELEASE_SCRIPT_PATH.read_text()

    npm_publish = script.index("npm publish --access public")
    npm_gate = script.index("if [[ $NPM_READY -eq 0 ]]")
    sdk_publish = script.index(
        "uv publish --check-url https://pypi.org/simple/ dist/sdk/*"
    )

    assert npm_publish < npm_gate < sdk_publish, (
        "release.sh must publish or verify the required TypeScript runtime "
        "before publishing an SDK whose generated scaffold requires it"
    )
