"""Unit tests for T7.10 GatePackagesAPI (ENG-4765) — kz.gates.packages.*."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from kamiwaza_sdk.exceptions import (
    GatePackageHashMismatchError,
    GatePackageHashRequiredError,
    GatePackageInstallTimeoutError,
    GatePackageNotFoundError,
    error_for_response,
)
from kamiwaza_sdk.schemas.gate_packages import (
    GatePackageInstallResult,
    GatePackageList,
    GatePackageState,
)
from kamiwaza_sdk.services.gate_packages import GatePackagesAPI
from kamiwaza_sdk.services.gates import GatesAPI


def _state_dict() -> Dict[str, Any]:
    return {
        "name": "acme-gates",
        "package_spec": "acme-gates==1.0.0",
        "version": "1.0.0",
        "hash_digest": "sha256:abc",
        "index_url": None,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "installed_by": "admin",
        "last_replaced_at": None,
        "status": "active",
        "classpaths": ["acme_gates.gate.AcmeAttributeGate"],
    }


class TestGatesLazyProperty:
    """Verify ``kz.gates.packages`` is lazy + cached + the right type."""

    def test_packages_returns_gate_packages_api(self, mock_client):
        gates = GatesAPI(mock_client)
        assert isinstance(gates.packages, GatePackagesAPI)

    def test_packages_is_cached_per_gates_instance(self, mock_client):
        gates = GatesAPI(mock_client)
        first = gates.packages
        second = gates.packages
        assert first is second

    def test_packages_only_instantiated_on_first_access(self, mock_client):
        gates = GatesAPI(mock_client)
        assert not hasattr(gates, "_packages")
        _ = gates.packages
        assert hasattr(gates, "_packages")


class TestInstall:
    """``kz.gates.packages.install``."""

    def test_posts_to_gate_packages_endpoint(self, mock_client):
        mock_client.expect(
            "POST",
            "/authz/gate-packages",
            {
                "package": _state_dict(),
                "install_duration_seconds": 2.3,
                "audit_event_id": "evt-1",
            },
        )
        svc = GatePackagesAPI(mock_client)
        result = svc.install("acme-gates==1.0.0", "sha256:abc")

        assert isinstance(result, GatePackageInstallResult)
        assert result.package.name == "acme-gates"
        assert result.install_duration_seconds == 2.3

        method, path, kwargs = mock_client.calls[0]
        assert (method, path) == ("POST", "/authz/gate-packages")
        body = kwargs["json"]
        assert body == {
            "package_spec": "acme-gates==1.0.0",
            "hash_digest": "sha256:abc",
        }

    def test_include_index_url_when_supplied(self, mock_client):
        mock_client.expect(
            "POST",
            "/authz/gate-packages",
            {"package": _state_dict(), "install_duration_seconds": 0.1},
        )
        svc = GatePackagesAPI(mock_client)
        svc.install(
            "acme-gates==1.0.0",
            "sha256:abc",
            index_url="https://idx.example/simple",
        )
        body = mock_client.calls[0][2]["json"]
        assert body["index_url"] == "https://idx.example/simple"


class TestListGet:
    """``kz.gates.packages.list`` + ``.get``."""

    def test_list_returns_typed(self, mock_client):
        mock_client.expect(
            "GET",
            "/authz/gate-packages",
            {"items": [_state_dict()], "total": 1, "page": 1, "per_page": 20},
        )
        result = GatePackagesAPI(mock_client).list()
        assert isinstance(result, GatePackageList)
        assert result.total == 1
        assert result.items[0].name == "acme-gates"

    def test_list_empty(self, mock_client):
        mock_client.expect(
            "GET",
            "/authz/gate-packages",
            {"items": [], "total": 0, "page": 1, "per_page": 20},
        )
        result = GatePackagesAPI(mock_client).list()
        assert result.items == []
        assert result.total == 0

    def test_get_returns_typed_state(self, mock_client):
        mock_client.expect("GET", "/authz/gate-packages/acme-gates", _state_dict())
        result = GatePackagesAPI(mock_client).get("acme-gates")
        assert isinstance(result, GatePackageState)
        assert result.name == "acme-gates"


class TestReplaceUninstall:
    """M5b methods: client-side surface is wired now; server returns 501
    until M5b T7.5 ships."""

    def test_replace_calls_put(self, mock_client):
        mock_client.expect(
            "PUT",
            "/authz/gate-packages/acme-gates",
            {"package": _state_dict(), "install_duration_seconds": 1.0},
        )
        result = GatePackagesAPI(mock_client).replace(
            "acme-gates", "acme-gates==1.0.1", "sha256:def"
        )
        assert isinstance(result, GatePackageInstallResult)
        method, path, kwargs = mock_client.calls[0]
        assert (method, path) == ("PUT", "/authz/gate-packages/acme-gates")
        assert kwargs["json"]["package_spec"] == "acme-gates==1.0.1"

    def test_uninstall_calls_delete(self, mock_client):
        # MockClient treats None as 'no expectation set'; use an empty dict
        # for the 204-No-Content equivalent. The SDK ignores the body anyway.
        mock_client.expect("DELETE", "/authz/gate-packages/acme-gates", {})
        result = GatePackagesAPI(mock_client).uninstall("acme-gates")
        assert result is None
        assert mock_client.calls[0][:2] == ("DELETE", "/authz/gate-packages/acme-gates")


class TestErrorDispatch:
    """T7.13 dispatch table integration with the GatePackages SDK."""

    def test_400_hash_required_maps_to_typed(self):
        err = error_for_response(
            400, {"detail": {"reason": "hash_required"}}, "missing hash"
        )
        assert isinstance(err, GatePackageHashRequiredError)

    def test_422_hash_mismatch_maps_to_typed(self):
        err = error_for_response(
            422, {"detail": {"reason": "hash_mismatch"}}, "bad hash"
        )
        assert isinstance(err, GatePackageHashMismatchError)

    def test_504_install_timeout_maps_to_typed(self):
        err = error_for_response(
            504, {"detail": {"reason": "install_timeout"}}, "slow"
        )
        assert isinstance(err, GatePackageInstallTimeoutError)

    def test_404_not_found_maps_to_typed(self):
        err = error_for_response(
            404, {"detail": {"reason": "gate_package_not_found"}}, "no such"
        )
        assert isinstance(err, GatePackageNotFoundError)
