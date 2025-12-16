from __future__ import annotations

import doctest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

DOCS_DIR = Path(__file__).resolve().parents[2] / "docs-local"


def test_pat_cli_guide_doctest():
    doc = DOCS_DIR / "pat-cli-guide.md"
    assert doc.exists(), f"Missing guide file: {doc}"
    failure_count, _ = doctest.testfile(
        str(doc),
        module_relative=False,
        optionflags=doctest.ELLIPSIS,
    )
    assert failure_count == 0
