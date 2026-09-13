"""ENG-11513: explicit, fail-loud NVIDIA whole-device/MIG API qualification.

Opt in with KAMIWAZA_TENANT_NVIDIA_REQUEST, a CreateModelDeployment JSON
request naming the owner's prepared model, config, file and NVIDIA profile.
No Node/inventory discovery, model import or inferred profile is performed.
Only an unconfigured lane skips; configured-lane failures fail the test.
Owner-side device/grant, identity and Kubernetes cleanup checks are separate.
"""

from __future__ import annotations

import os
import time
from typing import Callable
from uuid import UUID

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.serving.serving import (
    AcceleratorInferenceRequest,
    CreateModelDeployment,
)
from kamiwaza_sdk.services.serving import DEPLOYMENT_STATUS_REQUEST_TIMEOUT_SECONDS

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def load_request(raw: str) -> CreateModelDeployment:
    """Require a prepared file and explicit accelerator owner profile."""
    target = CreateModelDeployment.model_validate_json(raw)
    assert target.m_file_id is not None, "owner-qualified model file is required"
    resources = target.inference_resources
    assert isinstance(resources, AcceleratorInferenceRequest), "GPU resources required"
    profile = resources.accelerator.profile
    assert profile, "explicit owner profile is required"
    assert profile.startswith(
        "nvidia-"
    ), "NVIDIA qualification requires an owner profile in the nvidia-* namespace"
    assert not resources.alternatives, "qualification cannot use CPU fallback"
    assert target.engine_name == "llamacpp", "this lane qualifies llamacpp chat"
    assert not target.force_cpu, "qualification cannot force CPU execution"
    return target


def require_verified_tls() -> None:
    """Do not accept a live NVIDIA result obtained with TLS verification off."""
    value = os.environ.get("KAMIWAZA_VERIFY_SSL", "").strip().lower()
    assert value in {"1", "true", "yes", "on"}, (
        "NVIDIA qualification requires KAMIWAZA_VERIFY_SSL=true; "
        "disabled TLS is not release evidence"
    )


def invoke_twice(client: KamiwazaClient, deployment_id: UUID) -> None:
    """Both cold and warm calls must return actual nonempty chat content."""
    inference = client.openai.get_client(deployment_id=deployment_id).with_options(
        timeout=60, max_retries=0
    )
    try:
        for _ in range(2):
            response = inference.chat.completions.create(
                model="kamiwaza",
                messages=[{"role": "user", "content": "Say hello. /no_think"}],
                max_tokens=128,
                temperature=0,
            )
            assert response.choices, "inference returned no choices"
            assert (response.choices[0].message.content or "").strip(), "empty content"
    finally:
        inference.close()


def stop_and_verify(client: KamiwazaClient, deployment_id: UUID) -> None:
    """Stop failures or lingering API instances cannot be hidden by teardown."""
    assert client.serving.stop_deployment(
        deployment_id=deployment_id, force=True
    ), "stop refused"
    deadline = time.monotonic() + 90
    last_transient: APIError | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timeout_error = TimeoutError(
                "stop did not reach STOPPED with zero instances in 90s"
            )
            if last_transient is not None:
                raise timeout_error from last_transient
            raise timeout_error
        try:
            stopped = client.serving.get_deployment(
                deployment_id,
                timeout_seconds=min(
                    DEPLOYMENT_STATUS_REQUEST_TIMEOUT_SECONDS, remaining
                ),
            )
        except APIError as exc:
            # A status read can briefly fail while the serving control plane
            # rolls out the stop. Retry connection failures and 5xx responses,
            # but fail immediately for caller/authorization errors (4xx).
            if exc.status_code is not None and exc.status_code < 500:
                raise
            last_transient = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timeout_error = TimeoutError(
                    "stop did not reach STOPPED with zero instances in 90s"
                )
                raise timeout_error from last_transient
            time.sleep(min(1, remaining))
            continue
        if stopped.status == "STOPPED" and stopped.instances == []:
            return
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1, remaining))


def qualify(
    client: KamiwazaClient,
    target: CreateModelDeployment,
    record: Callable[[str, str], None],
) -> None:
    """Exercise the public lifecycle without weakening configured-lane failures."""
    resources = target.inference_resources
    assert isinstance(resources, AcceleratorInferenceRequest)
    profile = resources.accelerator.profile
    assert profile is not None
    payload = target.model_dump(by_alias=True, exclude_none=True)
    payload["model_id"] = payload.pop("m_id")
    deployment_id = client.serving.deploy_model(**payload, wait=False)
    assert isinstance(deployment_id, UUID), "deployment did not return an id"
    try:
        record("deployment_id", str(deployment_id))
        ready = client.serving.wait_deployment_ready(
            deployment_id, timeout_seconds=600, poll_interval_seconds=5
        )
        assert ready.status == "DEPLOYED", "deployment did not reach DEPLOYED"
        assert ready.instances, "deployment reported no instances"
        actual = client.serving.get_deployment(deployment_id).inference_resources
        assert (
            actual == target.inference_resources
        ), "inference resource readback changed"
        record("owner_profile", profile)
        invoke_twice(client, deployment_id)
    finally:
        stop_and_verify(client, deployment_id)


def test_explicit_nvidia_owner_lifecycle(request, record_property) -> None:
    raw = os.environ.get("KAMIWAZA_TENANT_NVIDIA_REQUEST", "")
    if not raw:
        pytest.skip("NVIDIA owner qualification lane is not configured")
    require_verified_tls()
    target = load_request(raw)
    try:
        client = request.getfixturevalue("live_kamiwaza_client")
    except pytest.skip.Exception as exc:
        pytest.fail(f"configured NVIDIA lane lacks a live client: {exc}")
    qualify(client, target, record_property)
