"""Unit cover for the extension scaffold's consumability gates.

ENG-12432. The scaffold test regenerates the project it inspects, so nothing
outside the assertions can hand them a corrupted manifest — which means a gate
could be removed without any test noticing. Driving the extracted seam directly
fixes that: each corruption below is a value the deploy stage rejects and a
hand-written type table would have admitted.
"""

import pytest

from tests.integration.test_extension_developer_path_live import (
    _assert_scaffold_is_consumable,
)

# The shape `kz-ext create --type app` emits, reduced to the fields the gates
# read. Verified against a real scaffold: this passes unchanged.
GOOD_MANIFEST = {
    "name": "eng12432check",
    "version": "0.1.0",
    "type": "app",
    "source_type": "user_repo",
    "visibility": "private",
    "description": "A Kamiwaza app extension",
    "risk_tier": 0,
    "verified": False,
    "kz_ext_version": ">=0.2.0,<1.0.0",
}
GOOD_COMPOSE = {"services": {"backend": {}, "frontend": {}}}


def test_a_real_scaffold_shape_passes() -> None:
    """The control. Without it, every case below could pass vacuously."""
    _assert_scaffold_is_consumable(dict(GOOD_MANIFEST), dict(GOOD_COMPOSE))


def test_a_risk_tier_outside_the_allowed_literals_is_refused() -> None:
    """An int, so a type check admits it; `Literal[0, 1, 2]` does not."""
    manifest = dict(GOOD_MANIFEST, risk_tier=3)
    with pytest.raises(Exception, match="risk_tier"):
        _assert_scaffold_is_consumable(manifest, dict(GOOD_COMPOSE))


def test_a_kz_ext_version_that_is_not_a_specifier_set_is_refused() -> None:
    """A non-empty str, so a type check admits it; the CLI contract does not."""
    manifest = dict(GOOD_MANIFEST, kz_ext_version="banana")
    with pytest.raises(AssertionError, match="CLI-contract check rejects"):
        _assert_scaffold_is_consumable(manifest, dict(GOOD_COMPOSE))


def test_a_services_list_is_refused() -> None:
    """Truthy, and the deploy stage iterates it as a mapping."""
    with pytest.raises(AssertionError, match="iterates it as a mapping"):
        _assert_scaffold_is_consumable(dict(GOOD_MANIFEST), {"services": ["echo"]})


def test_a_services_string_is_refused() -> None:
    with pytest.raises(AssertionError, match="iterates it as a mapping"):
        _assert_scaffold_is_consumable(dict(GOOD_MANIFEST), {"services": "broken"})


def test_an_empty_services_mapping_is_refused() -> None:
    with pytest.raises(AssertionError, match="iterates it as a mapping"):
        _assert_scaffold_is_consumable(dict(GOOD_MANIFEST), {"services": {}})
