"""Test fixtures for kamiwaza_extensions tests.

Round-3 review H4: several extension modules cache JSON-bundled resources
via ``@lru_cache(maxsize=1)`` (compatibility.json, exception_names.json,
runtime-lib pins). Once a single test reads any of those, subsequent
tests that monkeypatch the underlying file content see *cached* values
unless we clear explicitly. This autouse fixture clears all of them
between tests so monkeypatch-based mutations work as authors expect.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_extension_registry():
    """Keep the new extension-registry lookup from shelling out to a live
    kubectl/cluster across the whole unit suite (ENG-7051).

    ``resolve_image_registry`` now calls ``detect_extension_registry()`` before
    the core-config lookup. Suites that only patch ``detect_core_config_registry``
    (e.g. ``test_dev_canonical_refs``, ``test_doctor``) would otherwise resolve a
    real ``KAMIWAZA_EXTENSION_REGISTRY`` from the developer's current kube
    context. Default it to "unconfigured" (matching a fresh CI env); cases that
    exercise it re-patch this with a value (the decorator patch wins)."""
    try:
        import kamiwaza_extensions.registry_resolution  # noqa: F401
    except ImportError:
        yield
        return
    with patch(
        "kamiwaza_extensions.registry_resolution.detect_extension_registry",
        return_value=None,
    ):
        yield


@pytest.fixture(autouse=True)
def _clear_extensions_lru_caches():
    """Clear extension-module LRU caches between tests.

    Imports are inside the fixture so module load order doesn't matter
    (the kamiwaza_extensions package may not yet be importable during
    pytest's collection phase for unrelated test files).
    """
    yield
    try:
        from kamiwaza_extensions.doctor import (
            _compatibility_bundle,
            _uac_9d_hints,
        )

        _compatibility_bundle.cache_clear()
        _uac_9d_hints.cache_clear()
    except ImportError:
        pass
    try:
        from kamiwaza_extensions.scaffolder import _runtime_lib_pins

        _runtime_lib_pins.cache_clear()
    except ImportError:
        pass
