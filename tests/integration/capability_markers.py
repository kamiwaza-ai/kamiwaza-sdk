"""Parametric capability-marker logic for the live integration suite (M5).

Pure, cluster-free helpers that decide whether a capability-marked test can run
on the current host. The integration ``conftest`` wires these into a
``pytest.skip``-not-fail gate so under-provisioned hosts skip (never fail).

Markers (all skip-not-fail):

    @pytest.mark.min_gpu_count(N)         # >= N GPU devices across the cluster
    @pytest.mark.min_gpu_mem(GB)          # at least one GPU with >= GB memory
    @pytest.mark.gpu_vendor("nvidia")     # an nvidia GPU is present
    @pytest.mark.gpu_vendor("amd")        # an amd GPU is present
    @pytest.mark.gpu_vendor("none")       # CPU-only host (no GPU present)
    @pytest.mark.gpu_mig_support          # a MIG-capable GPU is present
    @pytest.mark.min_node_count(N)        # >= N running nodes

Design principle: when a capability cannot be *determined* from the cluster
inventory (e.g. the GPU dicts don't carry a memory field), the test is
**skipped, not failed** — an undeterminable capability is treated as unmet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

CAPABILITY_MARKER_NAMES = (
    "min_gpu_count",
    "min_gpu_mem",
    "gpu_vendor",
    "gpu_mig_support",
    "min_node_count",
)


@dataclass(frozen=True)
class ClusterCapabilitySnapshot:
    """A point-in-time view of the cluster's GPU/node inventory."""

    gpu_count: int = 0
    gpu_mem_gb: tuple[float, ...] = ()  # per-GPU memory in GB; empty => unknown
    gpu_vendors: frozenset[str] = frozenset()
    mig_supported: Optional[bool] = None  # None => undeterminable
    node_count: int = 0


# --------------------------------------------------------------------------- #
# Defensive parsing of the untyped GPU dicts (Hardware.gpus: List[Dict])
# --------------------------------------------------------------------------- #
_VENDOR_KEYS = ("vendor", "gpu_vendor", "brand", "manufacturer")
_NAME_KEYS = ("name", "model", "product", "product_name", "gpu_name")
_MEM_GB_KEYS = ("memory_gb", "mem_gb", "vram_gb")
_MEM_MB_KEYS = ("memory_mb", "mem_mb", "vram_mb", "memory_total_mb")
_MEM_BYTES_KEYS = ("memory_bytes", "memory_total", "mem_bytes", "total_memory")
_MIG_KEYS = ("mig", "mig_enabled", "mig_capable", "mig_support", "mig_supported")

_NVIDIA_HINTS = ("nvidia", "cuda", "tesla", "geforce", "quadro", "rtx")
_AMD_HINTS = ("amd", "radeon", "instinct", "rocm")


def _canon_vendor(value: str) -> Optional[str]:
    token = value.strip().lower()
    if not token:
        return None
    if token in ("nvidia", "amd", "none"):
        return token
    if any(hint in token for hint in _NVIDIA_HINTS):
        return "nvidia"
    if any(hint in token for hint in _AMD_HINTS):
        return "amd"
    return None


def _detect_vendor(gpu: dict) -> Optional[str]:
    for key in _VENDOR_KEYS:
        value = gpu.get(key)
        if isinstance(value, str) and value.strip():
            canon = _canon_vendor(value)
            if canon:
                return canon
    for key in _NAME_KEYS:
        value = gpu.get(key)
        if isinstance(value, str):
            canon = _canon_vendor(value)
            if canon:
                return canon
    return None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _detect_mem_gb(gpu: dict) -> Optional[float]:
    for key in _MEM_GB_KEYS:
        value = _num(gpu.get(key))
        if value is not None:
            return value
    for key in _MEM_MB_KEYS:
        value = _num(gpu.get(key))
        if value is not None:
            return value / 1024.0
    for key in _MEM_BYTES_KEYS:
        value = _num(gpu.get(key))
        if value is not None:
            return value / (1024.0**3)
    return None


def _detect_mig(gpu: dict) -> Optional[bool]:
    for key in _MIG_KEYS:
        if key in gpu:
            value = gpu[key]
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on", "enabled")
    return None


def _hardware_gpus(hardware: Any) -> Optional[list]:
    gpus = getattr(hardware, "gpus", None)
    if gpus is None and isinstance(hardware, dict):
        gpus = hardware.get("gpus")
    return gpus


def _hardware_node_key(hardware: Any) -> str:
    node_id = getattr(hardware, "node_id", None) or getattr(
        hardware, "local_node_id", None
    )
    if node_id is None and isinstance(hardware, dict):
        node_id = hardware.get("node_id") or hardware.get("local_node_id")
    return str(node_id) if node_id is not None else f"_anon_{id(hardware)}"


def build_capability_snapshot(
    hardware_entries: Iterable[Any],
    node_count: Optional[int] = None,
) -> ClusterCapabilitySnapshot:
    """Aggregate a cluster snapshot from SDK ``Hardware`` entries.

    ``hardware_entries`` may be SDK ``Hardware`` models or plain dicts.
    ``node_count`` should come from ``cluster.get_running_nodes()`` when
    available; otherwise it is inferred from distinct hardware node ids.
    """
    entries = list(hardware_entries or [])
    gpu_dicts: list[dict] = []
    for hardware in entries:
        gpus = _hardware_gpus(hardware)
        if gpus:
            gpu_dicts.extend(gpu for gpu in gpus if isinstance(gpu, dict))

    mem_gb: list[float] = []
    vendors: set[str] = set()
    mig_flags: list[bool] = []
    for gpu in gpu_dicts:
        mem = _detect_mem_gb(gpu)
        if mem is not None:
            mem_gb.append(mem)
        vendor = _detect_vendor(gpu)
        if vendor:
            vendors.add(vendor)
        mig = _detect_mig(gpu)
        if mig is not None:
            mig_flags.append(mig)

    if node_count is None:
        node_count = len({_hardware_node_key(hw) for hw in entries})

    return ClusterCapabilitySnapshot(
        gpu_count=len(gpu_dicts),
        gpu_mem_gb=tuple(mem_gb),
        gpu_vendors=frozenset(vendors),
        mig_supported=(any(mig_flags) if mig_flags else None),
        node_count=node_count or 0,
    )


# --------------------------------------------------------------------------- #
# Marker collection + evaluation
# --------------------------------------------------------------------------- #
def collect_capability_requirements(marks: Iterable[Any]) -> dict:
    """Build a requirements dict from pytest ``Mark`` objects (``.name``/``.args``)."""
    requirements: dict = {}
    for mark in marks:
        name = getattr(mark, "name", None)
        if name not in CAPABILITY_MARKER_NAMES:
            continue
        args = getattr(mark, "args", ()) or ()
        if name in ("min_gpu_count", "min_node_count"):
            requirements[name] = int(args[0]) if args else 1
        elif name == "min_gpu_mem":
            requirements[name] = float(args[0]) if args else 0.0
        elif name == "gpu_vendor":
            requirements[name] = str(args[0]).strip().lower() if args else "any"
        elif name == "gpu_mig_support":
            requirements[name] = True
    return requirements


def evaluate_capability_requirements(
    snapshot: Optional[ClusterCapabilitySnapshot],
    requirements: dict,
) -> Optional[str]:
    """Return a skip reason if any requirement is unmet/undeterminable, else None."""
    if snapshot is None:
        return (
            "cluster capability snapshot unavailable; cannot verify "
            + ", ".join(sorted(requirements))
        )

    for name, want in requirements.items():
        if name == "min_gpu_count":
            if snapshot.gpu_count < want:
                return f"requires >= {want} GPU(s); cluster reports {snapshot.gpu_count}"
        elif name == "min_node_count":
            if snapshot.node_count < want:
                return f"requires >= {want} node(s); cluster reports {snapshot.node_count}"
        elif name == "min_gpu_mem":
            if not snapshot.gpu_mem_gb:
                return (
                    f"requires a GPU with >= {want} GB; "
                    "GPU memory not reported by cluster inventory"
                )
            if max(snapshot.gpu_mem_gb) < want:
                return (
                    f"requires a GPU with >= {want} GB; "
                    f"largest GPU reports {max(snapshot.gpu_mem_gb):.1f} GB"
                )
        elif name == "gpu_vendor":
            if want == "none":
                if snapshot.gpu_count > 0:
                    return (
                        "requires a CPU-only host (gpu_vendor=none); "
                        f"cluster reports {snapshot.gpu_count} GPU(s)"
                    )
            elif want == "any":
                if snapshot.gpu_count == 0:
                    return "requires a GPU; cluster reports none"
            else:  # specific vendor: nvidia / amd / other
                if not snapshot.gpu_vendors:
                    return (
                        f"requires a {want} GPU; "
                        "GPU vendor not reported by cluster inventory"
                    )
                if want not in snapshot.gpu_vendors:
                    return (
                        f"requires a {want} GPU; "
                        f"cluster reports {sorted(snapshot.gpu_vendors)}"
                    )
        elif name == "gpu_mig_support":
            if snapshot.mig_supported is not True:
                state = "not reported" if snapshot.mig_supported is None else "unsupported"
                return f"requires a MIG-capable GPU; cluster MIG support {state}"
    return None
