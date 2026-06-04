from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from typing import Any

import requests
import urllib3

from . import auth_ops, build_ops, runtime_ops
from .common import logger


class LiveExtensionHarness:
    def __init__(self, settings: Any, client: Any) -> None:
        self.settings = settings
        self.client = client
        self.bootstrap_state = settings.bootstrap_state
        self.keep_deployments = os.getenv("LIVE_EXTENSION_KEEP_DEPLOYMENT", "").strip().lower() in {"1", "true", "yes", "on"}
        self.secret_encryption_key = os.getenv("LIVE_EXTENSION_SECRET_ENCRYPTION_KEY") or secrets.token_urlsafe(32)
        self.http = requests.Session()
        self.http.verify = settings.verify_ssl
        self._persona_clients: dict[str, Any] = {}
        self._bootstrap_clients: dict[str, Any] = {}
        if not settings.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def close(self) -> None:
        _best_effort_close(self.http, "harness HTTP session")
        _best_effort_close(self.client, "primary client")
        primary_bootstrap = getattr(self.client, "_bootstrap_client", None)
        if primary_bootstrap is not None:
            _best_effort_close(primary_bootstrap, "primary bootstrap client")
        for client in self._persona_clients.values():
            _best_effort_close(client, "persona client")
        for client in self._bootstrap_clients.values():
            _best_effort_close(client, "bootstrap client")

    def build_extension(self, contract: Any) -> None: build_ops.build_extension(self, contract)
    def push_app_template(self, contract: Any) -> None: build_ops.push_app_template(self, contract)
    def find_app_template(self, contract: Any) -> dict[str, Any]: return build_ops.find_app_template(self, contract)
    def pull_template_images(self, template_id: str) -> None: build_ops.pull_template_images(self, template_id)
    def deployment_env_vars(self, contract: Any) -> dict[str, str]: return build_ops.deployment_env_vars(self, contract)
    def deploy_app(self, template_id: str, contract: Any) -> dict[str, Any]: return runtime_ops.deploy_app(self, template_id, contract)
    def get_deployment(self, deployment_id: str) -> dict[str, Any]: return runtime_ops.get_deployment(self, deployment_id)
    def deployment_diagnostics(self, deployment_id: str) -> dict[str, Any]: return runtime_ops.deployment_diagnostics(self, deployment_id)
    def wait_for_deployment_logs(self, deployment_id: str, *, marker: str, request_id: str | None = None) -> dict[str, Any]: return runtime_ops.wait_for_deployment_logs(self, deployment_id, marker=marker, request_id=request_id)
    def wait_for_deployment(self, deployment_id: str) -> dict[str, Any]: return runtime_ops.wait_for_deployment(self, deployment_id)
    def deployment_url(self, deployment: dict[str, Any], contract: Any) -> str: return runtime_ops.deployment_url(self, deployment, contract)
    def app_url(self, deployment: dict[str, Any], contract: Any) -> str: return runtime_ops.app_url(self, deployment, contract)
    def readiness_url(self, deployment: dict[str, Any], contract: Any) -> str: return runtime_ops.readiness_url(self, deployment, contract)
    def smoke_url(self, deployment: dict[str, Any], contract: Any) -> str: return runtime_ops.smoke_url(self, deployment, contract)
    def write_deployment_artifact(self, deployment: dict[str, Any], contract: Any): return runtime_ops.write_deployment_artifact(self, deployment, contract)
    def wait_for_http_ok(self, url: str, headers: dict[str, str] | None = None) -> requests.Response: return runtime_ops.wait_for_http_ok(self, url, headers=headers)
    def wait_for_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]: return runtime_ops.wait_for_json(self, url, headers=headers)
    def auth_headers(self) -> dict[str, str]: return auth_ops.auth_headers(self)
    def persona(self, role_key: str): return auth_ops.persona(self, role_key)
    def client_for_role(self, role_key: str): return auth_ops.client_for_role(self, role_key)
    def auth_headers_for_role(self, role_key: str) -> dict[str, str]: return auth_ops.auth_headers_for_role(self, role_key)
    def probe_headers(self, contract: Any) -> dict[str, str] | None: return auth_ops.probe_headers(self, contract)
    def cleanup_deployment(self, deployment_id: str) -> None: runtime_ops.cleanup_deployment(self, deployment_id)


def best_effort_harness(harness: LiveExtensionHarness) -> Iterator[LiveExtensionHarness]:
    try:
        yield harness
    finally:
        harness.close()


def _best_effort_close(resource: Any, label: str) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:  # pragma: no cover - defensive teardown
            logger.warning("Failed to close %s: %s", label, exc)
