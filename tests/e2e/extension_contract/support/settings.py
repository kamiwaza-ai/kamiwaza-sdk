from __future__ import annotations

import os
from dataclasses import dataclass

import pytest
import requests
import urllib3

from .common import (
    env_flag,
    load_local_admin_password,
    logger,
    origin_from_base_url,
    ping_response_ok,
)
from .state import LiveRoutedIntegrationState


@dataclass(frozen=True)
class LiveExtensionSettings:
    base_url: str
    origin: str
    username: str | None
    password: str | None
    api_key: str | None
    verify_ssl: bool
    deployment_timeout_seconds: int
    probe_timeout_seconds: int
    bootstrap_state: LiveRoutedIntegrationState | None
    control_plane_role_key: str | None

    @classmethod
    def from_env(
        cls,
        bootstrap_state: LiveRoutedIntegrationState | None,
    ) -> LiveExtensionSettings:
        base_url = (
            os.getenv("KAMIWAZA_API_URL")
            or os.getenv("KAMIWAZA_BASE_URL")
            or (bootstrap_state.api_base_url if bootstrap_state else "")
            or "https://localhost/api"
        ).rstrip("/")
        origin = (
            os.getenv("KAMIWAZA_APP_ORIGIN")
            or os.getenv("KAMIWAZA_ORIGIN")
            or (bootstrap_state.app_origin if bootstrap_state else "")
            or origin_from_base_url(base_url)
        ).rstrip("/")
        username = os.getenv("KAMIWAZA_USERNAME")
        password = os.getenv("KAMIWAZA_PASSWORD")
        api_key = os.getenv("KAMIWAZA_API_KEY")
        control_plane_role_key: str | None = None
        if bootstrap_state and not api_key and not password:
            for role_key in ("admin", "owner_equivalent"):
                persona = bootstrap_state.personas.get(role_key)
                if persona is None:
                    continue
                resolved_api_key = bootstrap_state.resolve_api_key(persona)
                if resolved_api_key:
                    api_key = resolved_api_key
                    username = None
                    password = None
                    control_plane_role_key = role_key
                    break
                resolved_password = bootstrap_state.resolve_password(persona)
                if resolved_password:
                    username = persona.username
                    password = resolved_password
                    control_plane_role_key = role_key
                    break
        if not api_key and not password:
            logger.debug("No bootstrap-state credential resolved; falling back to local admin password lookup")
            password = load_local_admin_password()
            if password and not username:
                username = "admin"
                control_plane_role_key = "admin"
        # Default to TLS verification ON, matching tests/integration/gate_packages/
        # test_lifecycle.py — never silently insecure. To opt out for dev clusters
        # with self-signed certs, set KAMIWAZA_VERIFY_SSL=0 explicitly.
        verify_ssl = env_flag("KAMIWAZA_VERIFY_SSL", default=True)
        if "KAMIWAZA_VERIFY_SSL" not in os.environ and bootstrap_state is not None:
            verify_ssl = bootstrap_state.verify_ssl
        return cls(
            base_url=base_url,
            origin=origin,
            username=username,
            password=password,
            api_key=api_key,
            verify_ssl=verify_ssl,
            deployment_timeout_seconds=_timeout_seconds("LIVE_EXTENSION_DEPLOY_TIMEOUT", 900),
            probe_timeout_seconds=_timeout_seconds("LIVE_EXTENSION_PROBE_TIMEOUT", 180),
            bootstrap_state=bootstrap_state,
            control_plane_role_key=control_plane_role_key,
        )


def _timeout_seconds(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning("Invalid %s value %r; falling back to %s", name, raw_value, default)
        return default
    return max(1, parsed)


def assert_origin_ready(settings: LiveExtensionSettings) -> None:
    attempts: list[str] = []
    for target in (f"{settings.origin}/health", f"{settings.origin}/"):
        try:
            response = requests.get(target, timeout=5, verify=settings.verify_ssl)
        except requests.RequestException as exc:
            attempts.append(f"{target}: {exc}")
            continue
        if _origin_probe_succeeded(target, response):
            _finalize_origin_probe(settings)
            return
        attempts.append(f"{target}: {response.status_code} {response.text[:200]}")
    for target in (f"{settings.base_url}/health", f"{settings.base_url}/ping"):
        try:
            response = requests.get(target, timeout=5, verify=settings.verify_ssl)
        except requests.RequestException as exc:
            attempts.append(f"{target}: {exc}")
            continue
        if _api_probe_succeeded(target, response):
            _finalize_origin_probe(settings)
            return
        attempts.append(f"{target}: {response.status_code} {response.text[:200]}")
    pytest.fail("Kamiwaza origin health failed: " + " | ".join(attempts))


def _origin_probe_succeeded(target: str, response: requests.Response) -> bool:
    return response.status_code == 200


def _api_probe_succeeded(target: str, response: requests.Response) -> bool:
    return response.status_code == 200 or (
        target.endswith("/ping") and ping_response_ok(response)
    )


def _finalize_origin_probe(settings: LiveExtensionSettings) -> None:
    if not settings.verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
