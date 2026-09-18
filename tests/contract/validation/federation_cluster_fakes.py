"""Composed SDK cluster clients for federation lifecycle contracts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from kamiwaza_sdk.validation.federation_fixture import GATE_CLASSPATH, GATE_PACKAGE_SPEC
from tests.contract.validation.federation_test_support import _NotFound


class _Users:
    def __init__(self) -> None:
        self.added: list[str] = []

    def add(
        self, external_id: str, *, initial_tuples: list[dict[str, str]]
    ) -> dict[str, str]:
        del initial_tuples
        self.added.append(external_id)
        return {"id": external_id}


class _FederationProxy:
    def __init__(self) -> None:
        self.users = _Users()


class _Federations:
    def __init__(self, cluster_id: str, remote_id: str) -> None:
        self.cluster_id = cluster_id
        self.remote_id = remote_id
        self.proxies: dict[str, _FederationProxy] = {}
        self.id_proxies: dict[str, _FederationProxy] = {}
        self.deleted: list[str] = []
        self.revoked: set[str] = set()

    def pair(self, *, name: str, role: str, **kwargs: Any) -> dict[str, str]:
        del kwargs
        proxy = self.proxies.setdefault(name, _FederationProxy())
        federation_id = f"{role}-{self.cluster_id}-fed"
        self.id_proxies[federation_id] = proxy
        return {"id": federation_id, "name": name}

    def get(self, federation_id: str) -> dict[str, str]:
        del federation_id
        return {"remote_cluster_id": self.remote_id}

    def __getitem__(self, name: str) -> _FederationProxy:
        return self.proxies[name]

    def by_id(
        self, federation_id: str, *, remote_name: str | None = None
    ) -> _FederationProxy:
        del remote_name
        return self.id_proxies[federation_id]


class _ClusterAPI:
    def __init__(self) -> None:
        self.execution_gate_calls: list[tuple[str, dict[str, Any]]] = []

    def declare_attribute(self, name: str, *, type: str) -> None:
        del name, type

    def get_execution_gate(self) -> Any:
        raise _NotFound("no execution gate")

    def set_execution_gate(self, *, type: str, config: dict[str, Any]) -> None:
        self.execution_gate_calls.append((type, config))

    def clear_execution_gate(self) -> None:
        self.execution_gate_calls.append(("clear", {}))


class _Packages:
    def list(self) -> list[Any]:
        return [
            SimpleNamespace(
                name="acme-gates",
                package_spec=GATE_PACKAGE_SPEC,
                version="1.2.0",
                hash_digest="sha256:" + "0" * 64,
                status="active",
                classpaths=[GATE_CLASSPATH],
            )
        ]

    def uninstall(self, package_name: str) -> None:
        del package_name


class _Gates:
    def __init__(self) -> None:
        self.packages = _Packages()

    def discover(self, classpath: str) -> Any:
        assert classpath == GATE_CLASSPATH
        return SimpleNamespace(name="mini_access_tier_gate")


class _Datasets:
    def __init__(self) -> None:
        self.created: list[str] = []

    def create(self, **kwargs: Any) -> str:
        del kwargs
        urn = "urn:li:dataset:(urn:li:dataPlatform:file,/tmp/access_tier,PROD)"
        self.created.append(urn)
        return urn

    def set_gate(self, urn: str, *, type: str, config: dict[str, Any]) -> None:
        del urn, type, config

    def delete(self, urn: str) -> None:
        if urn not in self.created:
            raise _NotFound("dataset is already absent")
        self.created.remove(urn)


class _Client:
    def __init__(self, cluster_id: str, remote_id: str) -> None:
        self.cluster_id = cluster_id
        self.federations = _Federations(cluster_id, remote_id)
        self.datasets = _Datasets()
        self.gates = _Gates()
        self.cluster = _ClusterAPI()
        self.requests: list[tuple[str, str]] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        if method in {"DELETE", "POST"} and any(
            old_method == method and old_path == path
            for old_method, old_path in self.requests
        ):
            raise _NotFound("federation is already absent")
        self.requests.append((method, path))
        return {}

    def close(self) -> None:
        return None


class _ClusterWrapper:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def close(self) -> None:
        self.client.close()


class _ClusterFactory:
    def __init__(self) -> None:
        self.clients = {
            "edge-a": _ClusterWrapper(_Client("edge-a", "edge-b")),
            "edge-b": _ClusterWrapper(_Client("edge-b", "edge-a")),
        }

    def __call__(self, runtime_cluster: Any) -> _ClusterWrapper:
        return self.clients[str(runtime_cluster.id)]
