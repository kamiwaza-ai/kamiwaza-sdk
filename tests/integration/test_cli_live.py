from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from model_targets import InferenceTarget

from _kamiwaza_cli_live import (
    CliAuthConfig,
    _cleanup_cli_resources,
    _serve_deploy_args,
    assert_cli_pat_cache_matches,
    cli_login_and_create_pat,
    pat_jti,
    run_cli,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_cli_login_and_pat_flow(
    live_server_available: str,
    live_username: str,
    live_password: str,
    live_kamiwaza_client,
    tmp_path: Path,
) -> None:
    """CLI login + PAT creation/caching (no model deployment required)."""
    token_path = tmp_path / "token.json"
    base_args = ["--base-url", live_server_available, "--token-path", str(token_path)]

    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")

    # Cache verification runs inside cleanup protection after PAT creation.
    auth_config = CliAuthConfig(
        base_args,
        env,
        live_username,
        live_password,
        token_path,
    )
    pat_token = cli_login_and_create_pat(auth_config, pat_prefix="cli-m1")
    try:
        assert_cli_pat_cache_matches(token_path, pat_token)
    finally:
        live_kamiwaza_client.auth.revoke_pat(pat_jti(pat_token))


@pytest.mark.requires_deployable_model
def test_cli_serve_deploy(
    live_server_available: str,
    live_username: str,
    live_password: str,
    client_factory,
    live_kamiwaza_client,
    ensure_deployable_model_ready,
    deployable_model_target: InferenceTarget,
    target_model_file_id,
    tmp_path: Path,
) -> None:
    """CLI ``serve deploy`` round-trip.

    Requires a host that can actually deploy the test model; gated by
    ``requires_deployable_model`` so it skips (rather than fails) on hosts
    without compatible inference capacity for the platform-selected target.
    """
    token_path = tmp_path / "token.json"
    base_args = ["--base-url", live_server_available, "--token-path", str(token_path)]

    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")

    auth_config = CliAuthConfig(
        base_args,
        env,
        live_username,
        live_password,
        token_path,
    )
    pat_token = cli_login_and_create_pat(
        auth_config,
        pat_prefix="cli-deploy",
        scope="admin",
    )
    deployment_id: str | None = None

    try:
        assert_cli_pat_cache_matches(token_path, pat_token)
        pat_client = client_factory(base_url=live_server_available, api_key=pat_token)
        model = ensure_deployable_model_ready(pat_client)
        model_file_id = target_model_file_id(
            model, deployable_model_target.quantization
        )
        serve_result = run_cli(
            _serve_deploy_args(
                base_args,
                deployable_model_target,
                model_file_id,
            ),
            env,
        )

        summary = json.loads(serve_result.stdout.strip())
        deployment_id = summary.get("deployment_id")
        assert deployment_id, "CLI serve deploy did not return a deployment_id"
        deployment = pat_client.serving.wait_deployment_ready(
            deployment_id,
            timeout_seconds=600,
            poll_interval_seconds=5,
        )
        assert deployment.status == "DEPLOYED"
    finally:
        _cleanup_cli_resources(
            live_kamiwaza_client,
            deployment_id,
            pat_token,
        )
