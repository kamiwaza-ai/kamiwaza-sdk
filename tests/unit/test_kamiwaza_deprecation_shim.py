"""T7.14 / ENG-5048 — Deprecation shim for the legacy ``kamiwaza`` namespace.

WS-M3.2 deprecation. The legacy ``kamiwaza.Kamiwaza`` class emits a
one-time ``DeprecationWarning`` per process pointing customers at the
canonical ``kamiwaza_sdk.KamiwazaClient``. The class itself keeps
working — existing M1-M3 callsites and tests continue to function during
the gradual migration.

Per design AC-M3.2-2: the deprecation shim is a *courtesy* for the
gradual migration; the warning is the customer-visible signal that
removal is coming. Removal target: v2.0 per OQ-17.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest

pytestmark = pytest.mark.unit


def _reset_kamiwaza_module() -> None:
    """Drop the kamiwaza package from sys.modules so the next import
    re-runs __init__.py — needed because the deprecation shim's
    one-time-per-process state lives at module scope."""
    mods_to_drop = [name for name in sys.modules if name.startswith("kamiwaza.")]
    mods_to_drop.append("kamiwaza")
    for name in mods_to_drop:
        sys.modules.pop(name, None)


def test_legacy_kamiwaza_import_still_works() -> None:
    """``from kamiwaza import Kamiwaza`` is the M1-M3 customer-facing
    import path. T7.14 keeps it working (the shim is additive)."""
    from kamiwaza import Kamiwaza  # type: ignore[attr-defined]

    assert isinstance(Kamiwaza, type)


def test_instantiating_kamiwaza_emits_deprecation_warning() -> None:
    """``Kamiwaza(base_url=..., token=...)`` emits a DeprecationWarning
    naming the replacement (``kamiwaza_sdk.KamiwazaClient``)."""
    _reset_kamiwaza_module()
    from kamiwaza import Kamiwaza  # type: ignore[attr-defined]

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    dep_warnings = [w for w in recorded if issubclass(w.category, DeprecationWarning)]
    assert dep_warnings, "DeprecationWarning must fire on Kamiwaza() instantiation"
    msg = str(dep_warnings[0].message)
    assert "kamiwaza_sdk.KamiwazaClient" in msg, (
        f"Warning must name the replacement (kamiwaza_sdk.KamiwazaClient); got: {msg!r}"
    )


def test_deprecation_warning_fires_once_per_process() -> None:
    """A noisy warning that fires on every Kamiwaza() instantiation would
    spam logs and CI output. T7.14 uses module-level state to fire exactly
    one warning per process per OQ-17 contract."""
    _reset_kamiwaza_module()
    from kamiwaza import Kamiwaza  # type: ignore[attr-defined]

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        # Three instantiations — only the first should warn.
        Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
        Kamiwaza(base_url="https://kamiwaza.test", token="pat-def")
        Kamiwaza(base_url="https://kamiwaza.test", token="pat-ghi")

    dep_warnings = [w for w in recorded if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) == 1, (
        f"Expected exactly one DeprecationWarning per process; got {len(dep_warnings)}"
    )


def test_deprecation_warning_points_at_customer_call_site() -> None:
    """``stacklevel=2`` makes the warning point at the customer's import
    line, not at the shim itself. Otherwise customers chasing the
    warning end up reading the SDK internals instead of their own code."""
    _reset_kamiwaza_module()
    from kamiwaza import Kamiwaza  # type: ignore[attr-defined]

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    dep_warnings = [w for w in recorded if issubclass(w.category, DeprecationWarning)]
    assert dep_warnings, "DeprecationWarning must fire"
    # Warning's filename should be THIS test file (the caller), not the
    # kamiwaza/client.py shim. The stacklevel=2 in warnings.warn(...)
    # makes that work.
    assert "test_kamiwaza_deprecation_shim" in dep_warnings[0].filename


def test_legacy_imports_via_kamiwaza_package_still_work() -> None:
    """Existing M1-M3 imports like ``from kamiwaza.federations import FederationsAPI``
    continue to work via the per-module re-exports (T7.5/T7.6/etc.). The
    deprecation shim is additive — it doesn't remove import paths."""
    importlib.import_module("kamiwaza.federations")
    importlib.import_module("kamiwaza.jobs")
    importlib.import_module("kamiwaza.cluster")
    importlib.import_module("kamiwaza.subjects")
    importlib.import_module("kamiwaza.datasets")
    importlib.import_module("kamiwaza.gates")
    importlib.import_module("kamiwaza.retrieval")
    importlib.import_module("kamiwaza.exceptions")
    importlib.import_module("kamiwaza.models")


def test_removal_target_v2_named_in_warning() -> None:
    """Per OQ-17, the warning must name the removal target (v2.0) so
    customers know when to migrate by."""
    _reset_kamiwaza_module()
    from kamiwaza import Kamiwaza  # type: ignore[attr-defined]

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    dep_warnings = [w for w in recorded if issubclass(w.category, DeprecationWarning)]
    assert dep_warnings, "DeprecationWarning must fire"
    msg = str(dep_warnings[0].message)
    assert "v2.0" in msg or "2.0" in msg, (
        f"Warning must name the v2.0 removal target; got: {msg!r}"
    )
