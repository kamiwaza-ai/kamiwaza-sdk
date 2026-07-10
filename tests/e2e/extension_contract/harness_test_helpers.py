from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from .support.settings import LiveExtensionSettings
from .support.state import LivePersona, LiveRoutedIntegrationState


def settings_fixture(**overrides: object) -> LiveExtensionSettings:
    defaults: dict[str, object] = {
        "base_url": "https://localhost/api",
        "origin": "https://localhost",
        "username": "admin",
        "password": "secret",
        "api_key": None,
        "verify_ssl": False,
        "deployment_timeout_seconds": 60,
        "probe_timeout_seconds": 30,
        "bootstrap_state": None,
        "control_plane_role_key": None,
    }
    defaults.update(overrides)
    return LiveExtensionSettings(
        base_url=str(defaults["base_url"]),
        origin=str(defaults["origin"]),
        username=defaults["username"] if isinstance(defaults["username"], str) else None,
        password=defaults["password"] if isinstance(defaults["password"], str) else None,
        api_key=defaults["api_key"] if isinstance(defaults["api_key"], str) else None,
        verify_ssl=bool(defaults["verify_ssl"]),
        deployment_timeout_seconds=int(defaults["deployment_timeout_seconds"]),
        probe_timeout_seconds=int(defaults["probe_timeout_seconds"]),
        bootstrap_state=defaults["bootstrap_state"] if defaults["bootstrap_state"] is None or isinstance(defaults["bootstrap_state"], LiveRoutedIntegrationState) else None,
        control_plane_role_key=defaults["control_plane_role_key"] if isinstance(defaults["control_plane_role_key"], str) else None,
    )


def bootstrap_state_fixture(tmp_path: Path) -> LiveRoutedIntegrationState:
    return LiveRoutedIntegrationState(
        api_base_url="https://kamiwaza.test/api",
        app_origin="https://kamiwaza.test",
        verify_ssl=False,
        namespace="kamiwaza",
        personas=MappingProxyType({
            "admin": LivePersona("admin", "admin", "kamiwaza-user-admin", None),
            "allowed_non_admin": LivePersona("allowed_non_admin", "testuser", "kamiwaza-user-testuser", "viewer"),
        }),
        workrooms=MappingProxyType({
            "allowed_workroom_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "denied_workroom_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }),
        discovered_models=(
            MappingProxyType({
                "model_id": "model-1",
                "display_name": "Useful Model",
                "provider": "llamacpp",
                "selection_hint": "Useful Model",
            }),
        ),
        credential_resolution=MappingProxyType(
            {
                "type": "secret_ref",
                "namespace": "kamiwaza",
                "key": "password",
                "helper": MappingProxyType({"type": "kz_login", "path": ""}),
            }
        ),
        generated_at="2026-04-07T00:00:00+00:00",
        path=tmp_path / "bootstrap-state.json",
    )


def state_with(tmp_path: Path, **changes: object) -> LiveRoutedIntegrationState:
    return replace(bootstrap_state_fixture(tmp_path), **changes)
