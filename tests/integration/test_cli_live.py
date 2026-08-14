from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from _kamiwaza_cli_live import cli_login_and_create_pat, pat_jti, run_cli
from model_targets import InferenceTarget

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _serve_deploy_args(
    base_args: list[str],
    target: InferenceTarget,
    model_file_id: str | None,
) -> list[str]:
    args = [
        *base_args,
        "serve",
        "deploy",
        "--repo-id",
        target.repo_id,
        "--engine-name",
        target.engine_name,
    ]
    if model_file_id:
        args.extend(["--file-id", model_file_id])
    return [*args, "--wait", "--poll-interval", "5", "--timeout", "600"]


def _cleanup_cli_resources(
    pat_client,
    baseline_deployment_ids: set[str],
    deployment_id: str | None,
    pat_token: str,
) -> None:
    cleanup_failures: list[str] = []
    try:
        current_deployment_ids = {
            str(deployment.id)
            for deployment in pat_client.serving.list_deployments()
        }
        cleanup_ids = current_deployment_ids - baseline_deployment_ids
    except Exception as exc:
        cleanup_ids = set()
        cleanup_failures.append(
            f"deployment discovery: {type(exc).__name__}: {exc}"
        )
    if deployment_id:
        cleanup_ids.add(deployment_id)
    for cleanup_id in sorted(cleanup_ids):
        try:
            pat_client.serving.stop_deployment(
                deployment_id=cleanup_id,
                force=True,
            )
        except Exception as exc:
            cleanup_failures.append(
                f"deployment {cleanup_id}: {type(exc).__name__}: {exc}"
            )
    try:
        pat_client.auth.revoke_pat(pat_jti(pat_token))
    except Exception as exc:
        cleanup_failures.append(f"PAT cleanup: {type(exc).__name__}: {exc}")
    assert not cleanup_failures, "CLI live cleanup failed: " + "; ".join(
        cleanup_failures
    )


def test_cli_login_and_pat_flow(
    live_server_available: str,
    live_username: str,
    live_password: str,
    client_factory,
    tmp_path: Path,
) -> None:
    """CLI login + PAT creation/caching (no model deployment required)."""
    token_path = tmp_path / "token.json"
    base_args = ["--base-url", live_server_available, "--token-path", str(token_path)]

    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")

    # The helper asserts the session token, PAT, and cache match.
    pat_token = cli_login_and_create_pat(
        base_args, env, live_username, live_password, token_path, pat_prefix="cli-m1"
    )
    pat_client = client_factory(base_url=live_server_available, api_key=pat_token)
    pat_client.auth.revoke_pat(pat_jti(pat_token))


@pytest.mark.requires_deployable_model
def test_cli_serve_deploy(
    live_server_available: str,
    live_username: str,
    live_password: str,
    client_factory,
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

    pat_token = cli_login_and_create_pat(
        base_args,
        env,
        live_username,
        live_password,
        token_path,
        pat_prefix="cli-deploy",
        scope="admin",
    )
    pat_client = client_factory(base_url=live_server_available, api_key=pat_token)
    model = ensure_deployable_model_ready(pat_client)
    model_file_id = target_model_file_id(
        model, deployable_model_target.quantization
    )
    baseline_deployment_ids = {
        str(deployment.id) for deployment in pat_client.serving.list_deployments()
    }
    deployment_id: str | None = None

    try:
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
        assert summary.get("status") == "DEPLOYED"
    finally:
        _cleanup_cli_resources(
            pat_client,
            baseline_deployment_ids,
            deployment_id,
            pat_token,
        )
