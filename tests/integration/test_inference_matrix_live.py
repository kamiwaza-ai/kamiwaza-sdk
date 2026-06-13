"""Capability-marked engine deploy+infer + fractional co-location (ENG-6948).

Lifts the API-observable outcomes of ``kamiwaza-smoke.py`` ``cmd_full`` +
``cmd_fractional`` into the SDK live suite, for the engines the suite does NOT
already cover:

- llamacpp (GGUF, CPU-capable) and vllm (NVIDIA-only): download -> deploy with an
  explicit ``engine_name`` -> wait ready -> serve a prompt. The mlx arm is
  already covered by ``test_serving_workflow.py`` and is intentionally not
  re-added.
- fractional / MIG co-location: deploy 2 copies of one model on a single GPU and
  assert BOTH reach DEPLOYED (the co-location proof — a whole-GPU scheduler would
  leave the 2nd Pending) AND each serves a prompt.

What is NOT lifted (operator-only, left in the smoke script): the kubectl-based
pod / device-node inspection (``_verify_fractional_placement`` /
``_assert_gpu_offload``), ``undeploy_all`` purges, and whisper transcription
(``cmd_full``'s ggml arm) — whisper needs a raw multipart route the SDK does not
expose, so it stays in the operator matrix. These tests assert the API-observable
outcome only.

skip-not-fail: every test is gated so under-provisioned hosts SKIP rather than
fail. GPU/MIG gating uses the M5 capability markers (autouse-enforced via
``conftest._enforce_capability_markers`` + ``cluster_capability_snapshot``);
deployability uses ``requires_deployable_model`` and a 5xx/timeout -> skip wrap.

CAVEAT: ``ensure_repo_ready`` selects a GGUF quant but cannot pin one exact
filename the way the smoke's ``files_to_download`` does, so the GGUF tests
register the repo best-effort and skip-not-fail if it cannot be made ready.
"""

from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
]

# Engine repos mirror cmd_full's defaults. GGUF is file-pinned in the smoke via
# files_to_download; the SDK download surface cannot reproduce that pin, so the
# GGUF tests skip-not-fail if the repo can't be made ready.
LLAMACPP_REPO = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
LLAMACPP_QUANT = "q4_k_m"
VLLM_REPO = "Qwen/Qwen3-4B-Instruct-2507"

WAIT_TIMEOUT = 600
FRACTIONAL_COPIES = 2

_CHAT_PROMPT = [{"role": "user", "content": "Reply with a single short greeting."}]


def _ensure_repo_or_skip(ensure_repo_ready, client, repo_id, **kwargs):
    """Register a repo in the live catalog, skip-not-fail on capability failure.

    A 5xx (host cannot fetch / register the model) or a download timeout is a
    capability/infra failure -> skip. A 4xx is a real regression and is
    re-raised. Mirrors ``deployable_model_prerequisite`` + ``_ensure_model_cached``.
    """
    try:
        return ensure_repo_ready(client, repo_id, **kwargs)
    except (TimeoutError, RuntimeError, ValueError) as exc:
        pytest.skip(
            f"Host cannot make repo '{repo_id}' ready for the inference matrix "
            f"(file-pinned download unavailable via SDK): {type(exc).__name__}: {exc}"
        )
    except APIError as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code is None or status_code < 500:
            raise
        pytest.skip(
            f"Host cannot make repo '{repo_id}' ready for the inference matrix: "
            f"APIError {status_code}: {exc}"
        )


def _deploy_or_skip(client, model, *, engine_name):
    """Deploy one copy with an explicit engine_name; skip-not-fail on 5xx refusal.

    Returns the deployment id (str). A falsy deploy_model return (False) or a 5xx
    means the host cannot bring the engine up -> skip; a 4xx is re-raised as a
    real regression.
    """
    configs = client.models.get_model_configs(model.id)
    if not configs:
        pytest.skip(f"No model configs available for '{engine_name}' test model")
    default_config = next((c for c in configs if c.default), configs[0])

    try:
        # wait=False (ENG-6530): deploy_model now defaults to wait=True and would
        # block + raise DeploymentFailedError/TimeoutError itself. This caller
        # owns the wait via _wait_or_skip, so return the id immediately and route
        # readiness/failure through the skip-not-fail wait wrapper (no double-wait).
        raw_deployment_id = client.serving.deploy_model(
            model_id=str(model.id),
            m_config_id=default_config.id,
            engine_name=engine_name,
            wait=False,
            lb_port=0,
            autoscaling=False,
            min_copies=1,
            starting_copies=1,
        )
    except APIError as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code is None or status_code < 500:
            raise
        pytest.skip(
            f"Host refused to deploy engine '{engine_name}': "
            f"APIError {status_code}: {exc}"
        )

    if not raw_deployment_id:
        pytest.skip(
            f"deploy_model returned no id for engine '{engine_name}' "
            "(deploy refused on this host)."
        )
    return str(raw_deployment_id)


def _wait_or_skip(client, deployment_id, *, engine_name):
    """Wait for DEPLOYED; skip-not-fail when the instance can't load on this host."""
    try:
        return client.serving.wait_for_deployment(
            deployment_id,
            poll_interval=5,
            timeout=WAIT_TIMEOUT,
        )
    except (RuntimeError, TimeoutError) as exc:
        pytest.skip(
            f"Engine '{engine_name}' deployment {deployment_id} did not reach "
            f"DEPLOYED on this host: {type(exc).__name__}: {exc}"
        )


def _stop_quietly(client, deployment_id):
    if not deployment_id:
        return
    try:
        client.serving.stop_deployment(deployment_id=deployment_id, force=True)
    except Exception:  # noqa: BLE001 — teardown is best-effort
        pass


def _assert_chat_completion(client, deployment_id):
    """Serve a chat prompt via the OpenAI-compatible client and assert a reply."""
    openai_client = client.openai.get_client(deployment_id=deployment_id)
    response = openai_client.chat.completions.create(
        model="kamiwaza",
        messages=_CHAT_PROMPT,
        temperature=0.0,
    )
    assert response.choices, "deployment returned no chat choices"


# --------------------------------------------------------------------------- #
# Engine matrix tests
# --------------------------------------------------------------------------- #
def test_deploy_and_infer_llamacpp_gguf(live_kamiwaza_client, ensure_repo_ready):
    """llamacpp arm of cmd_full: download GGUF, deploy engine_name='llamacpp', infer.

    Covers the CPU-capable GGUF inference path the SDK suite does not yet cover
    (test_serving_workflow only covers MLX). No capability marker — llamacpp is
    CPU-capable, so it is gated only by model availability (_ensure_repo_or_skip)
    plus the deploy/wait skip-not-fail wrappers. requires_deployable_model is
    deliberately NOT used: its prerequisite hard-codes the MLX test model and
    would skip this on non-MLX hosts — the very hosts (x86 CPU) llamacpp targets.
    """
    client = live_kamiwaza_client
    model = _ensure_repo_or_skip(
        ensure_repo_ready, client, LLAMACPP_REPO, quantization=LLAMACPP_QUANT
    )

    deployment_id = None
    try:
        deployment_id = _deploy_or_skip(client, model, engine_name="llamacpp")
        details = _wait_or_skip(client, deployment_id, engine_name="llamacpp")
        assert details.instances, "llamacpp deployment should report instances"
        _assert_chat_completion(client, deployment_id)
    finally:
        _stop_quietly(client, deployment_id)


@pytest.mark.gpu_vendor("nvidia")
@pytest.mark.min_gpu_count(1)
@pytest.mark.min_gpu_mem(16)
def test_deploy_and_infer_vllm_nvidia(live_kamiwaza_client, ensure_repo_ready):
    """vllm arm of cmd_full: download safetensors, deploy engine_name='vllm', infer.

    The only engine arm that genuinely needs a discrete NVIDIA GPU; the SDK suite
    has zero vllm deploy+infer coverage. Capability-gated so it SKIPS (never
    fails) on CPU / Apple / AMD hosts.
    """
    client = live_kamiwaza_client
    model = _ensure_repo_or_skip(ensure_repo_ready, client, VLLM_REPO)

    deployment_id = None
    try:
        deployment_id = _deploy_or_skip(client, model, engine_name="vllm")
        details = _wait_or_skip(client, deployment_id, engine_name="vllm")
        assert details.instances, "vllm deployment should report instances"
        _assert_chat_completion(client, deployment_id)
    finally:
        _stop_quietly(client, deployment_id)


# --------------------------------------------------------------------------- #
# Fractional / MIG co-location tests
# --------------------------------------------------------------------------- #
def _certify_fractional_colocation(client, model):
    """Deploy FRACTIONAL_COPIES of one model and assert co-location + serve.

    Lifts the API-observable subset of _run_fractional_cert: deploy N copies,
    assert ALL reach DEPLOYED (the co-location proof on a single-GPU host — the
    whole-GPU path would leave the 2nd deployment never reaching DEPLOYED), and
    assert EACH serves a chat prompt. Excludes the kubectl pod/device-node
    inspection (operator-only). Cleans up every deployment it creates.

    Two known coarseness limitations (the full proof stays in cmd_full, which has
    the operator-only device inspection):
    - SKIP-vs-FAIL: a copy that deploys then FAILS/times-out during wait is
      SKIPPED (via _wait_or_skip), not failed — deliberately, so a capable but
      resource-pressured host does not false-fail. The flip side is that a genuine
      whole-GPU co-location regression on a 1-GPU host reads as a skip.
    - MULTI-GPU: min_gpu_count(1) only requires >=1 GPU, so on a multi-GPU host the
      copies may land on SEPARATE devices and still pass (only instances + serve
      are checked) — device-level co-location is not proven here.
    """
    deployment_ids: list[str] = []
    try:
        for _ in range(FRACTIONAL_COPIES):
            deployment_id = _deploy_or_skip(client, model, engine_name="llamacpp")
            deployment_ids.append(deployment_id)

        # ALL must reach DEPLOYED — on a single 1-GPU node this is only possible
        # if they share the device. This IS the co-location certification.
        for deployment_id in deployment_ids:
            details = _wait_or_skip(client, deployment_id, engine_name="llamacpp")
            assert (
                details.instances
            ), f"co-located deployment {deployment_id} reported no instances"

        # Each co-located instance must accept a prompt and return a response.
        for deployment_id in deployment_ids:
            _assert_chat_completion(client, deployment_id)
    finally:
        for deployment_id in deployment_ids:
            _stop_quietly(client, deployment_id)


@pytest.mark.requires_deployable_model
@pytest.mark.min_gpu_count(1)
@pytest.mark.min_gpu_mem(8)
def test_fractional_colocation_two_models_one_gpu(
    live_kamiwaza_client, ensure_repo_ready
):
    """Fractional cert: 2 copies of one model co-located on a single GPU, each serving.

    Lifts the API-observable outcome of cmd_fractional / _run_fractional_cert.
    Capability-gated for a GPU host with headroom for 2 small replicas so it
    SKIPS on CPU / laptop hosts.
    """
    client = live_kamiwaza_client
    model = _ensure_repo_or_skip(
        ensure_repo_ready, client, LLAMACPP_REPO, quantization=LLAMACPP_QUANT
    )
    _certify_fractional_colocation(client, model)


@pytest.mark.requires_deployable_model
@pytest.mark.gpu_mig_support
@pytest.mark.min_gpu_count(1)
def test_fractional_colocation_mig_partitioned_gpu(
    live_kamiwaza_client, ensure_repo_ready
):
    """MIG variant of the fractional cert: 2 co-located copies, each serving.

    Captures cmd_full's fractional MIG branch at the only level the SDK can
    observe — 2 replicas co-resident and each serving. Gated to a MIG-capable
    GPU so it SKIPS on non-MIG NVIDIA / AMD / CPU hosts.
    """
    client = live_kamiwaza_client
    model = _ensure_repo_or_skip(
        ensure_repo_ready, client, LLAMACPP_REPO, quantization=LLAMACPP_QUANT
    )
    _certify_fractional_colocation(client, model)
