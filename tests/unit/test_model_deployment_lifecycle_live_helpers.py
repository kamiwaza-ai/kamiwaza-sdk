"""Offline tests for the T09 live test's cleanup and prerequisite helpers (ENG-12327).

These branches only run when a live run fails or a prerequisite is missing, so
they are exercised here with fake clients instead of a cluster.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from kamiwaza_sdk.exceptions import KamiwazaError, NotFoundError

INTEGRATION_DIR = Path(__file__).resolve().parents[1] / "integration"
sys.path.insert(0, str(INTEGRATION_DIR))
live = importlib.import_module("test_model_deployment_lifecycle_live")
model_targets = importlib.import_module("model_targets")
pytestmark = pytest.mark.unit

QUANT = "q4_k"


@pytest.fixture(scope="module")
def integration_conftest():
    spec = importlib.util.spec_from_file_location(
        "_integration_conftest_t09_helpers_under_test", INTEGRATION_DIR / "conftest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _file(name: str, *, ready: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid4()),
        name=name,
        storage_location="/models/x" if ready else None,
        is_downloading=not ready,
        dl_requested_at=None,
    )


def _target(*, required: bool) -> object:
    return model_targets.InferenceTarget(
        repo_id="org/model-GGUF",
        engine_name="llamacpp",
        quantization=QUANT,
        required=required,
    )


SPLIT = ("model-Q4_K_M-00001-of-00002.gguf", "model-Q4_K_M-00002-of-00002.gguf")
READINESS_CASES = {
    "single ready file": [_file("model-Q4_K_M.gguf", ready=True)],
    "single file downloading": [_file("model-Q4_K_M.gguf", ready=False)],
    "split, every shard ready": [_file(n, ready=True) for n in SPLIT],
    "split, one shard downloading": [
        _file(SPLIT[0], ready=True),
        _file(SPLIT[1], ready=False),
    ],
    "no files": [],
    "only a different quantization": [_file("model-Q8_0.gguf", ready=True)],
}


@pytest.mark.parametrize("case", sorted(READINESS_CASES))
def test_readiness_matches_integration_conftest(case, integration_conftest):
    model = SimpleNamespace(m_files=READINESS_CASES[case])
    assert live._all_target_files_ready(model, QUANT) == (
        integration_conftest._model_has_ready_target_files(model, QUANT)
    )


def _lane_client(files: list) -> SimpleNamespace:
    model = SimpleNamespace(id=uuid4(), repo_modelId="org/model-GGUF", m_files=files)
    return SimpleNamespace(
        models=SimpleNamespace(list_models=lambda **_kwargs: [model])
    )


@pytest.mark.parametrize(
    ("required", "outcome"),
    [(False, pytest.skip.Exception), (True, pytest.fail.Exception)],
)
def test_partial_split_download_is_a_missing_prerequisite(
    required, outcome, integration_conftest
):
    client = _lane_client(READINESS_CASES["split, one shard downloading"])
    with pytest.raises(outcome, match="are not all downloaded"):
        live._ready_lane(
            client,
            _target(required=required),
            integration_conftest._target_model_file_id,
        )


def test_complete_split_download_yields_the_lane(integration_conftest):
    client = _lane_client(READINESS_CASES["split, every shard ready"])
    lane = live._ready_lane(
        client, _target(required=True), integration_conftest._target_model_file_id
    )
    assert isinstance(lane.file_id, UUID)
    assert lane.target.required is True


class _Configs:
    """A single config whose reads and deletes behave as a scenario dictates."""

    def __init__(self, *, name="owned", delete_mode="ok", first_read="ok") -> None:
        self.name = name
        self.delete_mode = delete_mode
        self.first_read = first_read
        self.present = True
        self.reads = 0

    def get_model_config(self, _config_id):
        self.reads += 1
        if self.reads == 1 and self.first_read == "404":
            raise NotFoundError("hidden", status_code=404)
        if not self.present:
            raise NotFoundError("gone", status_code=404)
        return SimpleNamespace(name=self.name)

    def delete_model_config(self, _config_id) -> None:
        if self.delete_mode == "error":
            raise KamiwazaError("boom", status_code=500)
        if self.delete_mode != "refused":
            self.present = False
        if self.delete_mode in ("refused", "404-but-deleted"):
            raise NotFoundError("not found", status_code=404)


def _cleanup(configs: _Configs) -> str | None:
    client = SimpleNamespace(models=configs)
    return live._config_cleanup_failure(client, uuid4(), "owned")


@pytest.mark.parametrize("delete_mode", ["ok", "404-but-deleted"])
def test_config_cleanup_passes_when_the_read_back_proves_absence(delete_mode):
    assert _cleanup(_Configs(delete_mode=delete_mode)) is None


def test_refused_delete_answering_404_is_reported_with_its_404():
    failure = _cleanup(_Configs(delete_mode="refused"))
    assert failure is not None
    assert "still readable after delete" in failure
    assert "(the delete answered" in failure


def test_non_404_delete_error_is_reported():
    failure = _cleanup(_Configs(delete_mode="error"))
    assert failure is not None and failure.startswith("could not delete config")


def test_renamed_config_is_not_deleted():
    configs = _Configs(name="someone-else")
    failure = _cleanup(configs)
    assert failure is not None and "refused to delete config" in failure
    assert configs.present


def test_first_read_404_is_not_taken_as_proof_of_removal():
    failure = _cleanup(_Configs(first_read="404"))
    assert failure is not None
    assert "could not confirm config" in failure


def test_cleanup_collects_stop_and_config_failures_together():
    serving = SimpleNamespace(
        stop_deployment=lambda **_kw: False,
        wait_for_deployment=lambda *_a, **_kw: None,
    )
    client = SimpleNamespace(serving=serving, models=_Configs(delete_mode="refused"))
    registry = live._Created()
    registry.deployment_ids.append(uuid4())
    registry.configs[uuid4()] = "owned"
    failures = live._cleanup_failures(client, registry)
    assert len(failures) == 2
    assert failures[0].startswith("could not stop deployment")
    assert "still readable after delete" in failures[1]


@pytest.mark.parametrize(
    ("required", "outcome"),
    [(False, pytest.skip.Exception), (True, pytest.fail.Exception)],
)
def test_missing_prerequisite_fails_only_a_required_target(required, outcome):
    with pytest.raises(outcome, match="no model config"):
        live._missing_prerequisite(_target(required=required), "no model config")
