"""Supply Kubernetes host discovery for SDK-built sandbox services."""

from typing import Any, Dict, List


def ensure_sandbox_host_ip_env(env: List[Dict[str, Any]]) -> None:
    """Fill missing/blank host IP only for an explicit Kubernetes backend.

    Extension create/patch bypasses App Garden's compose-time injection. Send
    the Downward API entry in the payload so host discovery needs no node read.
    Explicit values and existing valueFrom sources remain caller-owned.
    """
    backend = next(
        (entry for entry in env if entry.get("name") == "SANDBOX_BACKEND"), {}
    )
    if backend.get("value") != "kubernetes":
        return
    host_ip = {
        "name": "SANDBOX_HOST_IP",
        "valueFrom": {"fieldRef": {"fieldPath": "status.hostIP"}},
    }
    for index, entry in enumerate(env):
        if entry.get("name") != "SANDBOX_HOST_IP":
            continue
        if entry.get("valueFrom") is not None:
            return
        if str(entry.get("value", "")).strip():
            return
        env[index] = host_ip
        return
    env.append(host_ip)
