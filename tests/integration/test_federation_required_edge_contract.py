"""Registration contract for the required federation edge policy.

The fail-closed hooks for ``--require-federation-edge`` live in
``tests.integration.required_federation_edge``. pytest only honours
``pytest_plugins`` in the rootdir conftest, so the policy is inert unless that
list names the module. These cases fail when the entry is dropped.
"""

import pytest

from tests.integration import required_federation_edge as required_edge

pytestmark = pytest.mark.unit

PLUGIN_NAME = "tests.integration.required_federation_edge"


def test_required_federation_edge_plugin_is_registered_at_pytest_root(
    pytestconfig: pytest.Config,
) -> None:
    """The policy module is a live plugin in this session."""
    assert pytestconfig.pluginmanager.hasplugin(PLUGIN_NAME)


def test_registered_plugin_carries_the_fail_closed_hooks(
    pytestconfig: pytest.Config,
) -> None:
    """The registered module is the one holding the skip and selection guards."""
    plugin = pytestconfig.pluginmanager.get_plugin(PLUGIN_NAME)

    assert plugin is required_edge
    assert callable(plugin.pytest_runtest_makereport)
    assert callable(plugin.pytest_collection_finish)
