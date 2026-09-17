"""OpenAI-compatible inference through the SDK client (ENG-12327, T09).

Target selection and the prerequisite rule are shared with
``test_model_deployment_lifecycle_live.py`` and described in
``model_deployment_live_support``.

One chat completion, through the OpenAI-compatible client the SDK returns for a
fresh deployment, asking for the sum of two numbers chosen for this run; the
reply must contain the sum, which the prompt does not. This test carries only
the inference capability, so an inference failure cannot mark local deployment
as failing; it has to deploy before it can infer, so a deployment failure fails
it too.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator

import pytest
from tests.integration.model_deployment_live_support import (
    READY_TIMEOUT_SECONDS,
    DeploymentRegistry,
    assert_deployed_as_requested,
    deploy_fresh,
    lane_target,
    missing_prerequisite,
    ready_target,
    registry_with_cleanup,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.slow,
]


@pytest.fixture
def created(live_kamiwaza_client) -> Iterator[DeploymentRegistry]:
    """Stop the deployment the test body leaves running."""
    yield from registry_with_cleanup(live_kamiwaza_client)


def _sum_question() -> tuple[str, str]:
    """A prompt asking for the sum of two numbers chosen for this run, and the sum.

    The sum never appears in the prompt, so a reply that echoes the prompt or a
    fixed reply cannot contain it except by chance.
    """
    while True:
        left = 11 + secrets.randbelow(80)
        right = 11 + secrets.randbelow(80)
        answer = str(left + right)
        prompt = f"What is {left} plus {right}? Reply with only the number."
        if answer not in prompt:
            return prompt, answer


def test_openai_compatible_inference_through_sdk_client(
    live_kamiwaza_client,
    target_model_file_id,
    created,
) -> None:
    client = live_kamiwaza_client
    target = lane_target()
    model, file_id = ready_target(client, target, target_model_file_id)

    configs = client.models.get_model_configs(model.id)
    if not configs:
        missing_prerequisite(
            target,
            f"prerequisite: {model.repo_modelId} has no model config to deploy with",
        )
    config = next((c for c in configs if c.default), configs[0])

    deployment_id = deploy_fresh(client, target, model, config.id, file_id)
    created.deployment_ids.append(deployment_id)
    client.serving.wait_deployment_ready(
        deployment_id, timeout_seconds=READY_TIMEOUT_SECONDS
    )
    assert_deployed_as_requested(
        client.serving.get_deployment(deployment_id),
        deployment_id,
        target,
        config.id,
        file_id,
    )

    prompt, answer = _sum_question()
    openai_client = client.openai.get_client(deployment_id=deployment_id)
    try:
        served = openai_client.models.list().data
        assert served, "the deployment's OpenAI-compatible endpoint lists no models"
        reply = openai_client.chat.completions.create(
            model=served[0].id,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=16,
        )
    finally:
        openai_client.close()

    assert reply.choices, "chat completion returned no choices"
    content = reply.choices[0].message.content or ""
    assert re.search(rf"(?<!\d){answer}(?!\d)", content), (
        f"reply to {prompt!r} does not contain the sum {answer}: {reply!r}"
    )
    assert reply.usage is not None and reply.usage.prompt_tokens > 0, reply
    assert reply.usage.completion_tokens > 0, reply
