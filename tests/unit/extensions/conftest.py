"""Test fixtures for kamiwaza_extensions tests.

Round-3 review H4: several extension modules cache JSON-bundled resources
via ``@lru_cache(maxsize=1)`` (compatibility.json, exception_names.json,
runtime-lib pins). Once a single test reads any of those, subsequent
tests that monkeypatch the underlying file content see *cached* values
unless we clear explicitly. This autouse fixture clears all of them
between tests so monkeypatch-based mutations work as authors expect.
"""

from __future__ import annotations

import pytest


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
