"""Per-target federation credential resolution and attachment contracts."""

from __future__ import annotations

import json

import pytest

from tests.unit.test_kamiwaza_sdk_services_federations import _MockClient

pytestmark = pytest.mark.unit


def test_resolve_federation_credential_from_env() -> None:
    from kamiwaza_sdk.services.federation_credentials import (
        resolve_federation_credential,
    )

    env = {"KAMIWAZA_FEDERATION_CREDENTIAL_ORION": "offline-cred-xyz"}
    assert resolve_federation_credential("ORION", env=env) == "offline-cred-xyz"


def test_resolve_federation_credential_sanitizes_target_name() -> None:
    from kamiwaza_sdk.services.federation_credentials import (
        resolve_federation_credential,
    )

    env = {"KAMIWAZA_FEDERATION_CREDENTIAL_ORION_PROD": "cred"}
    assert resolve_federation_credential("orion-prod", env=env) == "cred"


def test_resolve_federation_credential_none_when_unset() -> None:
    from kamiwaza_sdk.services.federation_credentials import (
        resolve_federation_credential,
    )

    assert resolve_federation_credential("ORION", env={}) is None


def test_resolve_federation_credential_file_fallback(tmp_path) -> None:
    from kamiwaza_sdk.services.federation_credentials import (
        resolve_federation_credential,
    )

    cred_file = tmp_path / "creds.json"
    cred_file.write_text(json.dumps({"ORION": "from-file"}))
    env = {"KAMIWAZA_FEDERATION_CREDENTIAL_FILE": str(cred_file)}
    assert resolve_federation_credential("ORION", env=env) == "from-file"


def test_resolve_federation_credential_env_beats_file(tmp_path) -> None:
    from kamiwaza_sdk.services.federation_credentials import (
        resolve_federation_credential,
    )

    cred_file = tmp_path / "creds.json"
    cred_file.write_text(json.dumps({"ORION": "from-file"}))
    env = {
        "KAMIWAZA_FEDERATION_CREDENTIAL_ORION": "from-env",
        "KAMIWAZA_FEDERATION_CREDENTIAL_FILE": str(cred_file),
    }
    assert resolve_federation_credential("ORION", env=env) == "from-env"


def test_probe_attaches_federation_credential_header_when_resolved(monkeypatch) -> None:
    """Receiver-realm mesh calls carry their receiver-issued credential."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    monkeypatch.setenv("KAMIWAZA_FEDERATION_CREDENTIAL_ORION", "offline-cred-xyz")
    client = _MockClient()
    client.expect(
        "GET",
        "/mesh/ORION/api/cluster/cluster_capabilities",
        {"system_type": "linux", "os": "linux"},
    )
    FederationsAPI(client)["ORION"].probe()

    call = [kw for m, p, kw in client.calls if p.endswith("/cluster_capabilities")][0]
    assert call["headers"]["X-KZ-Federation-Credential"] == "offline-cred-xyz"


def test_probe_omits_federation_credential_header_when_absent(monkeypatch) -> None:
    from kamiwaza_sdk.services.federations import FederationsAPI

    monkeypatch.delenv("KAMIWAZA_FEDERATION_CREDENTIAL_ORION", raising=False)
    client = _MockClient()
    client.expect(
        "GET",
        "/mesh/ORION/api/cluster/cluster_capabilities",
        {"system_type": "linux", "os": "linux"},
    )
    FederationsAPI(client)["ORION"].probe()

    call = [kw for m, p, kw in client.calls if p.endswith("/cluster_capabilities")][0]
    headers = call.get("headers") or {}
    assert "X-KZ-Federation-Credential" not in headers
