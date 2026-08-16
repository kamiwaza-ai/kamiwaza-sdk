"""Stable package boundaries for neutral delegated-workload integrations."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from kamiwaza_sdk.delegated_workloads.transport import DelegatedWorkloadTransport


PUBLIC_MODULES = (
    "kamiwaza_sdk.delegated_workloads.client",
    "kamiwaza_sdk.delegated_workloads.executor",
    "kamiwaza_sdk.delegated_workloads.resource_server",
    "kamiwaza_sdk.delegated_workloads.adapters",
)
ADAPTER_PORTS = (
    "WorkloadRegistrationAdapter",
    "ResourceRegistrationAdapter",
    "ResourceCanonicalizer",
    "ResourceEntitlementAdapter",
    "QuotaAdapter",
    "BrokerOperationAdapter",
    "SafeResultNormalizer",
)
ADAPTER_METHODS = {
    "WorkloadRegistrationAdapter": "reconcile_workload",
    "ResourceRegistrationAdapter": "reconcile_resource",
    "ResourceCanonicalizer": "canonicalize",
    "ResourceEntitlementAdapter": "authorize",
    "QuotaAdapter": "reserve",
    "BrokerOperationAdapter": "execute",
    "SafeResultNormalizer": "normalize",
}
FORBIDDEN_IMPORT_PARTS = frozenset(
    {"extensions", "kamiwaza_extensions", "operators", "tomo"}
)


@pytest.mark.parametrize("module_name", PUBLIC_MODULES)
def test_distinct_public_modules_are_importable(module_name: str) -> None:
    assert importlib.import_module(module_name).__name__ == module_name


def test_client_and_resource_server_roles_are_separate() -> None:
    client = importlib.import_module(PUBLIC_MODULES[0])
    executor = importlib.import_module(PUBLIC_MODULES[1])
    resource_server = importlib.import_module(PUBLIC_MODULES[2])

    assert client.DelegatedWorkloadClient is not resource_server.DelegatedResourceServer
    assert client.DelegatedControlPlaneClient is not executor.DelegatedExecutorClient
    assert not hasattr(resource_server, "DelegatedExecutorClient")
    assert "register" not in client.DelegatedWorkloadClient.__dict__
    assert "register" not in resource_server.DelegatedResourceServer.__dict__


def test_workload_client_builds_only_role_specific_clients() -> None:
    client = importlib.import_module(PUBLIC_MODULES[0])
    executor = importlib.import_module(PUBLIC_MODULES[1])
    transport = cast(DelegatedWorkloadTransport, object())
    workload = client.DelegatedWorkloadClient("https://core.example.test/", transport)

    assert isinstance(workload.control_plane(), client.DelegatedControlPlaneClient)
    assert isinstance(workload.executor(), executor.DelegatedExecutorClient)


def test_registrar_and_resource_adapter_ports_are_protocols() -> None:
    adapters = importlib.import_module(PUBLIC_MODULES[3])

    for name in ADAPTER_PORTS:
        value = getattr(adapters, name)
        assert value._is_protocol  # type: ignore[attr-defined]
        assert hasattr(value, "adapter_id")
        assert hasattr(value, ADAPTER_METHODS[name])


def test_root_exports_stable_neutral_entry_points() -> None:
    delegated = importlib.import_module("kamiwaza_sdk.delegated_workloads")
    expected = {
        "DelegatedWorkloadClient",
        "DelegatedResourceServer",
        *ADAPTER_PORTS,
    }

    assert expected.issubset(set(delegated.__all__))
    assert all(getattr(delegated, name) is not None for name in expected)


def test_public_boundary_has_no_consumer_specific_import() -> None:
    modules = tuple(importlib.import_module(name) for name in PUBLIC_MODULES)
    imported = {
        target
        for module in modules
        for target in _import_targets(module)
    }

    assert not {
        target
        for target in imported
        if FORBIDDEN_IMPORT_PARTS.intersection(target.split("."))
    }


def _import_targets(module: ModuleType) -> set[str]:
    path = Path(module.__file__ or "")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
    return targets
