"""Unit tests for the parametric capability-marker logic (M5).

The pure helpers live in ``tests/integration/capability_markers.py`` so the
integration ``conftest`` can wire them into a ``pytest.skip``-not-fail gate.
These tests exercise the pure logic with synthetic inventory — no live cluster.
"""

from __future__ import annotations

import sys
from collections import namedtuple
from pathlib import Path

import pytest

# The helper is co-located with the integration conftest that consumes it.
# Add that directory to the path so this unit test can import it directly
# without making ``tests`` a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import capability_markers as cap  # noqa: E402

pytestmark = pytest.mark.unit

_Mark = namedtuple("_Mark", ["name", "args"])


def _hw(gpus, **overrides):
    """A hardware-entry dict shaped like the SDK ``Hardware`` model."""
    return {"gpus": gpus, "node_id": "node-1", **overrides}


# --------------------------------------------------------------------------- #
# build_capability_snapshot
# --------------------------------------------------------------------------- #
def test_build_snapshot_counts_gpus_across_hardware_entries():
    snap = cap.build_capability_snapshot(
        [_hw([{"vendor": "NVIDIA"}, {"vendor": "NVIDIA"}]), _hw([{"vendor": "NVIDIA"}])],
        node_count=2,
    )
    assert snap.gpu_count == 3
    assert snap.node_count == 2


def test_build_snapshot_parses_memory_mb_to_gb():
    snap = cap.build_capability_snapshot([_hw([{"memory_mb": 81920}])])
    assert snap.gpu_mem_gb and snap.gpu_mem_gb[0] == pytest.approx(80.0, abs=0.01)


def test_build_snapshot_parses_memory_bytes_to_gb():
    snap = cap.build_capability_snapshot([_hw([{"memory_total": 24 * 1024**3}])])
    assert snap.gpu_mem_gb and snap.gpu_mem_gb[0] == pytest.approx(24.0, abs=0.01)


def test_build_snapshot_parses_platform_vram_string_to_gb():
    snap = cap.build_capability_snapshot([_hw([{"type": "cuda", "vram": "31.00"}])])
    assert snap.gpu_vendors == frozenset({"nvidia"})
    assert snap.gpu_mem_gb and snap.gpu_mem_gb[0] == pytest.approx(31.0, abs=0.01)


def test_build_snapshot_detects_vendor_from_explicit_key():
    snap = cap.build_capability_snapshot([_hw([{"vendor": "AMD"}])])
    assert snap.gpu_vendors == frozenset({"amd"})


def test_build_snapshot_detects_vendor_from_model_name():
    snap = cap.build_capability_snapshot([_hw([{"name": "NVIDIA A100-SXM4-80GB"}])])
    assert snap.gpu_vendors == frozenset({"nvidia"})


def test_build_snapshot_mig_supported_true_when_any_gpu_reports_mig():
    snap = cap.build_capability_snapshot(
        [_hw([{"vendor": "nvidia", "mig_capable": False}, {"vendor": "nvidia", "mig_capable": True}])]
    )
    assert snap.mig_supported is True


def test_build_snapshot_mig_undeterminable_when_key_absent():
    snap = cap.build_capability_snapshot([_hw([{"vendor": "nvidia"}])])
    assert snap.mig_supported is None


def test_build_snapshot_empty_inventory_is_cpu_only():
    snap = cap.build_capability_snapshot([], node_count=1)
    assert snap.gpu_count == 0
    assert snap.gpu_mem_gb == ()
    assert snap.node_count == 1


def test_build_snapshot_ignores_explicitly_inactive_hardware_entries():
    snap = cap.build_capability_snapshot(
        [
            {"active": False, "node_id": "old", "gpus": [{"type": "cuda", "vram": "31"}]},
            {"active": True, "node_id": "new", "gpus": [{"type": "cuda", "vram": "24"}]},
        ],
        node_count=1,
    )
    assert snap.gpu_count == 1
    assert snap.gpu_mem_gb == (24.0,)


def test_build_snapshot_ignores_inactive_hardware_for_fallback_node_count():
    snap = cap.build_capability_snapshot(
        [
            {"active": False, "node_id": "old", "gpus": [{"type": "cuda", "vram": "31"}]},
            {"active": True, "node_id": "new", "gpus": []},
        ]
    )

    assert snap.node_count == 1


def test_build_snapshot_detects_apple_silicon_from_real_inventory():
    snap = cap.build_capability_snapshot(
        [
            _hw(
                [],
                processors=["Apple M4"],
                os="Darwin",
                platform="macOS-15.4-arm64-arm-64bit",
            )
        ]
    )

    assert snap.os_platforms == frozenset({("darwin", "macos-15.4-arm64-arm-64bit")})
    assert snap.is_apple_silicon is True


def test_build_snapshot_ignores_platform_from_synthetic_inventory_rows():
    snap = cap.build_capability_snapshot(
        [
            {
                "active": None,
                "gpus": None,
                "processors": None,
                "os": "Darwin",
                "platform": "arm64",
            },
            _hw(
                [],
                processors=["AMD EPYC"],
                os="Linux",
                platform="Linux-5.14.0-el9.x86_64",
            ),
        ]
    )

    assert snap.os_platforms == frozenset({("linux", "linux-5.14.0-el9.x86_64")})
    assert snap.is_apple_silicon is False


def test_build_snapshot_preserves_os_platform_correlation_for_mixed_nodes():
    snap = cap.build_capability_snapshot(
        [
            _hw(
                [],
                node_id="darwin-x86",
                processors=["Intel"],
                os="Darwin",
                platform="macOS-15.4-x86_64-i386-64bit",
            ),
            _hw(
                [],
                node_id="linux-arm",
                processors=["Ampere"],
                os="Linux",
                platform="Linux-6.8-aarch64-with-glibc2.39",
            ),
        ]
    )

    assert snap.is_apple_silicon is False


def test_build_snapshot_rejects_apple_only_inference_when_platform_rows_are_incomplete():
    snap = cap.build_capability_snapshot(
        [
            _hw(
                [],
                node_id="apple",
                processors=["Apple M4"],
                os="Darwin",
                platform="macOS-15.4-arm64-arm-64bit",
            ),
            _hw(
                [],
                node_id="unknown-platform",
                processors=["AMD EPYC"],
            ),
        ]
    )

    assert snap.platform_inventory_complete is False
    assert snap.is_apple_silicon is False


# --------------------------------------------------------------------------- #
# collect_capability_requirements
# --------------------------------------------------------------------------- #
def test_collect_requirements_reads_marker_args():
    marks = [
        _Mark("min_gpu_count", (2,)),
        _Mark("min_gpu_mem", (40,)),
        _Mark("gpu_vendor", ("nvidia",)),
        _Mark("gpu_mig_support", ()),
        _Mark("min_node_count", (2,)),
        _Mark("integration", ()),  # ignored — not a capability marker
    ]
    req = cap.collect_capability_requirements(marks)
    assert req == {
        "min_gpu_count": 2,
        "min_gpu_mem": 40.0,
        "gpu_vendor": "nvidia",
        "gpu_mig_support": True,
        "min_node_count": 2,
    }


# --------------------------------------------------------------------------- #
# evaluate_capability_requirements  (returns a skip reason, or None to run)
# --------------------------------------------------------------------------- #
def _snap(**kw):
    return cap.ClusterCapabilitySnapshot(**kw)


def test_evaluate_none_snapshot_always_skips():
    reason = cap.evaluate_capability_requirements(None, {"min_gpu_count": 1})
    assert reason and "unavailable" in reason


def test_evaluate_min_gpu_count_skips_when_insufficient():
    assert cap.evaluate_capability_requirements(_snap(gpu_count=1), {"min_gpu_count": 2})


def test_evaluate_min_gpu_count_runs_when_sufficient():
    assert cap.evaluate_capability_requirements(_snap(gpu_count=4), {"min_gpu_count": 2}) is None


def test_evaluate_min_node_count_skips_when_insufficient():
    assert cap.evaluate_capability_requirements(_snap(node_count=1), {"min_node_count": 2})


def test_evaluate_min_gpu_mem_skips_when_memory_undeterminable():
    reason = cap.evaluate_capability_requirements(
        _snap(gpu_count=1, gpu_mem_gb=()), {"min_gpu_mem": 40.0}
    )
    assert reason and "not reported" in reason


def test_evaluate_min_gpu_mem_skips_when_too_small():
    assert cap.evaluate_capability_requirements(
        _snap(gpu_mem_gb=(24.0,)), {"min_gpu_mem": 40.0}
    )


def test_evaluate_min_gpu_mem_runs_when_large_enough():
    assert (
        cap.evaluate_capability_requirements(_snap(gpu_mem_gb=(24.0, 80.0)), {"min_gpu_mem": 40.0})
        is None
    )


def test_evaluate_gpu_vendor_nvidia_runs_when_present():
    assert (
        cap.evaluate_capability_requirements(
            _snap(gpu_count=1, gpu_vendors=frozenset({"nvidia"})), {"gpu_vendor": "nvidia"}
        )
        is None
    )


def test_evaluate_gpu_vendor_nvidia_skips_when_absent():
    assert cap.evaluate_capability_requirements(
        _snap(gpu_count=1, gpu_vendors=frozenset({"amd"})), {"gpu_vendor": "nvidia"}
    )


def test_evaluate_gpu_vendor_skips_when_vendor_undeterminable():
    reason = cap.evaluate_capability_requirements(
        _snap(gpu_count=1, gpu_vendors=frozenset()), {"gpu_vendor": "nvidia"}
    )
    assert reason and "not reported" in reason


def test_evaluate_gpu_vendor_none_requires_cpu_only_host():
    assert cap.evaluate_capability_requirements(
        _snap(gpu_count=1), {"gpu_vendor": "none"}
    )
    assert (
        cap.evaluate_capability_requirements(_snap(gpu_count=0), {"gpu_vendor": "none"}) is None
    )


def test_evaluate_gpu_mig_support_skips_when_unsupported_or_unreported():
    assert cap.evaluate_capability_requirements(_snap(mig_supported=False), {"gpu_mig_support": True})
    assert cap.evaluate_capability_requirements(_snap(mig_supported=None), {"gpu_mig_support": True})
    assert (
        cap.evaluate_capability_requirements(_snap(mig_supported=True), {"gpu_mig_support": True})
        is None
    )
