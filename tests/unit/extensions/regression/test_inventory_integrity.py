"""Inventory integrity tests for the D210 extension regression harness.

Enforces the contract from system-design §4.2.13 / EDX-OPS-2:
- inventory.yaml is the versioned source of truth for the 12-issue checklist.
- Every automated entry's test_id must resolve to a real pytest node carrying
  the `extension_regression` marker.
- Every manual entry's runbook_ref must point to an existing anchor in the
  regression manual.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
INVENTORY = Path(__file__).parent / "inventory.yaml"
RUNBOOK = REPO_ROOT / "docs" / "extensions" / "runbook" / "regression-manual.md"

REG_ID_RE = re.compile(r"^REG-\d{3}$")
ENG_ID_RE = re.compile(r"^ENG-\d+$")
TEST_ID_RE = re.compile(r"^(tests/[^:]+\.py)::([A-Za-z_][A-Za-z0-9_]*)$")


def _load_inventory() -> list[dict]:
    with INVENTORY.open() as f:
        items = yaml.safe_load(f)
    assert isinstance(items, list), "inventory.yaml must be a YAML list"
    return items


@pytest.mark.unit
def test_inventory_has_exactly_twelve_items():
    items = _load_inventory()
    assert len(items) == 12, (
        f"D210 regression inventory must contain exactly 12 items per UAC-17 "
        f"(found {len(items)}). Update the system design and the milestone "
        f"description if the count is intentionally changing."
    )


@pytest.mark.unit
def test_inventory_ids_are_unique_and_well_formed():
    items = _load_inventory()
    ids = [item["id"] for item in items]
    assert len(set(ids)) == len(ids), f"Duplicate REG ids: {ids}"
    for rid in ids:
        assert REG_ID_RE.match(rid), f"Malformed regression id: {rid!r}"


@pytest.mark.unit
def test_origin_issues_are_well_formed():
    for item in _load_inventory():
        origin = item.get("origin_issue", "")
        assert ENG_ID_RE.match(
            origin
        ), f"{item['id']}: origin_issue {origin!r} must be a Linear ENG-NNNN id"


@pytest.mark.unit
def test_each_item_is_either_automated_or_manual_with_required_field():
    for item in _load_inventory():
        rid = item["id"]
        if item.get("automated"):
            assert "test_id" in item, f"{rid}: automated item missing test_id"
            assert (
                "runbook_ref" not in item
            ), f"{rid}: automated item must not also carry runbook_ref"
        else:
            assert (
                "runbook_ref" in item
            ), f"{rid}: non-automated item missing runbook_ref"
            assert (
                "test_id" not in item
            ), f"{rid}: manual item must not also carry test_id"


@pytest.mark.unit
def test_automated_test_ids_resolve_to_existing_test_nodes():
    """For every automated item, the test file exists and contains the named class/function."""
    for item in _load_inventory():
        if not item.get("automated"):
            continue
        rid = item["id"]
        test_id = item["test_id"]
        match = TEST_ID_RE.match(test_id)
        assert match, f"{rid}: test_id {test_id!r} not in '<path>::<name>' form"
        rel_path, name = match.groups()
        target = REPO_ROOT / rel_path
        assert target.exists(), f"{rid}: test file {rel_path} does not exist"
        source = target.read_text()
        # Allow either a class definition or a top-level function.
        if not (
            re.search(rf"^class {re.escape(name)}\b", source, re.MULTILINE)
            or re.search(rf"^def {re.escape(name)}\b", source, re.MULTILINE)
        ):
            pytest.fail(f"{rid}: {name!r} not found in {rel_path}")


@pytest.mark.unit
def test_manual_items_resolve_to_runbook_anchors():
    if not any(not item.get("automated") for item in _load_inventory()):
        pytest.skip("no manual items in inventory")
    assert RUNBOOK.exists(), f"regression manual missing at {RUNBOOK}"
    runbook_text = RUNBOOK.read_text()
    for item in _load_inventory():
        if item.get("automated"):
            continue
        rid = item["id"]
        ref = item["runbook_ref"]
        # ref is "<path>#<anchor>"; we only verify the anchor here.
        anchor = ref.split("#", 1)[1] if "#" in ref else ""
        assert anchor, f"{rid}: runbook_ref {ref!r} missing #anchor"
        # Anchor matches a heading whose slugified form equals the anchor.
        # Accept either an explicit {#anchor} attribute or a heading whose
        # lowercased + hyphenated text matches.
        slug = anchor.lower()
        headings = re.findall(r"^#{1,6}\s+(.+?)\s*$", runbook_text, re.MULTILINE)
        slugs = {re.sub(r"[^a-z0-9]+", "-", h.lower()).strip("-") for h in headings}
        # Accept exact slug match, slug-prefix match (heading starts with the
        # anchor like "## REG-002 — Description" → reg-002-description), or an
        # explicit {#anchor} attribute.
        matched = (
            slug in slugs
            or any(s == slug or s.startswith(f"{slug}-") for s in slugs)
            or f"{{#{anchor}}}" in runbook_text
        )
        assert matched, f"{rid}: anchor #{anchor} not found in regression manual"


@pytest.mark.unit
def test_automated_tests_carry_extension_regression_marker():
    """Every automated test_id must carry @pytest.mark.extension_regression
    at module, class (decorator or class-body ``pytestmark``), or function
    level. Drift here means ``pytest -m extension_regression`` silently
    omits a regression item from the D210-candidate replay."""
    for item in _load_inventory():
        if not item.get("automated"):
            continue
        rid = item["id"]
        rel_path, name = TEST_ID_RE.match(item["test_id"]).groups()
        source = (REPO_ROOT / rel_path).read_text()

        # Module-level pytestmark covers everything in the file.
        if re.search(r"^pytestmark\s*=.*extension_regression", source, re.MULTILINE):
            continue

        # Decorators directly above a `class Name:` or `def name(...)`.
        target_pattern = rf"((?:^@[^\n]*\n)+)^(?:class|def) {re.escape(name)}\b"
        m = re.search(target_pattern, source, re.MULTILINE)
        if m and "extension_regression" in m.group(1):
            continue

        # Class-body `pytestmark` — pytest honors `class X: pytestmark = [...]`
        # the same way as the module-level form. Scope the search to the
        # class body so a marker on a sibling class doesn't false-pass.
        class_body = re.search(
            rf"^class {re.escape(name)}\b.*?(?=^class |\Z)",
            source,
            re.MULTILINE | re.DOTALL,
        )
        if class_body and re.search(
            r"^\s+pytestmark\s*=.*extension_regression",
            class_body.group(0),
            re.MULTILINE,
        ):
            continue

        pytest.fail(
            f"{rid}: {name} in {rel_path} is missing the extension_regression "
            f"marker (looked at module-level pytestmark, decorators above the "
            f"target, and class-body pytestmark)."
        )
