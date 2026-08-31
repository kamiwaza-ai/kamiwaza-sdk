"""Contract tests for the SDK-owned delegated-workload provider."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kamiwaza_sdk.validation import ValidationProfile
from kamiwaza_sdk.validation.delegated_workload_cases import run_edge
from kamiwaza_sdk.validation.delegated_workload_provider import (
    DelegatedWorkloadLifecycleProvider,
    delegated_package_config_from_selection,
    main as delegated_provider_main,
)
from kamiwaza_sdk.validation.delegated_workload_spec import (
    DELEGATED_CASE_IDS,
    DELEGATED_FEATURE_ID,
    DELEGATED_SCENARIO_ID,
    delegated_package_config,
    scenario_descriptor,
)
from kamiwaza_sdk.validation.federation_cases import RunContext
from kamiwaza_sdk.validation.models import ResolvedScenario
from kamiwaza_sdk.validation.provider import ProviderContractError
from tests.contract.validation.support import profile_payload

pytestmark = pytest.mark.contract


def _profile(*, enabled: bool = True, include: bool = False) -> ValidationProfile:
    payload = profile_payload()
    payload["validation"] = {
        "level": "comprehensive",
        "fixture_mode": "owned",
        "include": [DELEGATED_SCENARIO_ID] if include else [],
        "exclude": [],
    }
    payload["clusters"] = [
        {
            "id": "edge-a",
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {DELEGATED_FEATURE_ID: enabled},
        },
        {
            "id": "edge-b",
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {},
        },
    ]
    payload["mesh"] = {
        "edges": [
            {
                "initiator": "edge-a",
                "receiver": "edge-b",
                "identity_mode": "shared_idp",
                "capabilities": [DELEGATED_FEATURE_ID] if enabled else [],
            }
        ]
    }
    payload.pop("inference_targets", None)
    return ValidationProfile.model_validate(payload)


def test_descriptor_is_comprehensive_and_capability_gated() -> None:
    descriptor = scenario_descriptor()

    assert descriptor.scenario_id == DELEGATED_SCENARIO_ID
    assert descriptor.target_scope == "mesh_edge"
    assert descriptor.minimum_level == "comprehensive"
    assert descriptor.case_ids == DELEGATED_CASE_IDS
    assert descriptor.applies_when[0].path == ("edge", "identity_mode")
    assert descriptor.applies_when[1].path == ("edge", "capabilities")


def test_provider_rejects_a_revision_override() -> None:
    with pytest.raises(ProviderContractError, match="revision is fixed"):
        DelegatedWorkloadLifecycleProvider(provider_revision="delegated@test")


def test_provider_rejects_unsupported_fixture_mode() -> None:
    profile = _profile()
    validation = profile.validation.model_copy(update={"fixture_mode": "external"})
    with pytest.raises(ProviderContractError, match="does not support fixture mode"):
        DelegatedWorkloadLifecycleProvider().resolve(
            profile.model_copy(update={"validation": validation})
        )


def test_selection_config_requires_a_resolved_scenario() -> None:
    with pytest.raises(ProviderContractError, match="selection parameters"):
        delegated_package_config_from_selection(SimpleNamespace())


def test_provider_prepare_edge_persists_package_fixture_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _run_context(_Persona([])).selected
    updated: dict[str, Any] = {}
    context = SimpleNamespace(
        selected=selected,
        store=SimpleNamespace(
            update_edge=lambda state, target_id, values: updated.update(
                {"state": state, "target_id": target_id, "values": values}
            )
            or "updated"
        ),
    )
    monkeypatch.setattr(
        "kamiwaza_sdk.validation.delegated_workload_provider.prepare_edge",
        lambda _context: "prepared",
    )

    assert DelegatedWorkloadLifecycleProvider()._prepare_edge(context) == "updated"
    assert updated["state"] == "prepared"
    assert updated["values"]["python_packages"] == [
        "humanize==4.13.0",
        "kamiwaza-sdk==1.1.0",
    ]


def test_provider_main_delegates_to_json_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kamiwaza_sdk.validation.cli.provider_main",
        lambda provider, argv: 7,
    )

    assert delegated_provider_main(["describe", "--json"]) == 7


def test_resolution_selects_enabled_edge_and_publishes_exact_fixture_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-delegated-1")
    monkeypatch.setenv(
        "KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON",
        '["humanize==4.13.0", "kamiwaza-sdk==1.1.0"]',
    )
    monkeypatch.setenv(
        "KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON",
        '["humanize", "kamiwaza_sdk"]',
    )

    plan = DelegatedWorkloadLifecycleProvider().resolve(_profile())

    assert len(plan.selected) == 1
    selected = plan.selected[0]
    assert selected.scenario_id == DELEGATED_SCENARIO_ID
    assert selected.case_ids == DELEGATED_CASE_IDS
    assert selected.cluster_ids == ("edge-a", "edge-b")
    assert selected.redacted_parameters["python_packages"] == [
        "humanize==4.13.0",
        "kamiwaza-sdk==1.1.0",
    ]
    assert selected.redacted_parameters["package_imports"] == [
        "humanize",
        "kamiwaza_sdk",
    ]


def test_resolution_omits_optional_scenario_without_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON", raising=False)
    monkeypatch.delenv("KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON", raising=False)

    plan = DelegatedWorkloadLifecycleProvider().resolve(_profile(enabled=False))

    assert plan.selected == ()
    assert plan.install_requirements == {}


def test_explicit_scenario_without_capability_fails_at_resolution() -> None:
    with pytest.raises(ProviderContractError):
        DelegatedWorkloadLifecycleProvider().resolve(
            _profile(enabled=False, include=True)
        )


@pytest.mark.parametrize(
    ("packages", "imports", "message"),
    [
        (None, '["humanize"]', "package fixture is not configured"),
        ('["humanize>=4.0"]', '["humanize"]', "exact"),
        (
            '["humanize==4.13.0", "kamiwaza-sdk==1.1.0"]',
            '["bad-name!", "kamiwaza_sdk"]',
            "import name",
        ),
    ],
)
def test_package_fixture_configuration_fails_closed(
    packages: str | None,
    imports: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if packages is None:
        monkeypatch.delenv("KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON", raising=False)
    else:
        monkeypatch.setenv("KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON", packages)
    monkeypatch.setenv("KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON", imports)

    with pytest.raises(ProviderContractError, match=message):
        delegated_package_config()


class _Persona:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = iter(results)
        self.session = SimpleNamespace(verify=True)
        self.jobs = SimpleNamespace(run=self._run)
        self.requests: list[tuple[str, str]] = []

    def _run(self, **kwargs: Any) -> Any:
        assert kwargs["delegated_access"]["datasets"]
        if kwargs.get("python_packages") is not None:
            assert kwargs["python_packages"] == [
                "humanize==4.13.0",
                "kamiwaza-sdk==1.1.0",
            ]
        payload = next(self._results)
        return SimpleNamespace(
            status="SUCCEEDED",
            result=payload,
            job_id="job-1",
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        del kwargs
        self.requests.append((method, path))
        return {
            "id": "job-1",
            "source": "mesh",
            "source_cluster_id": "edge-a",
            "receiver_cluster_id": "edge-b",
        }

    def close(self) -> None:
        return None


def _run_context(persona: _Persona) -> RunContext:
    selected = ResolvedScenario(
        target_id="mesh-edge:sha256:test",
        cluster_id="edge-a",
        cluster_ids=("edge-a", "edge-b"),
        scenario_id=DELEGATED_SCENARIO_ID,
        required=True,
        case_ids=DELEGATED_CASE_IDS,
        redacted_parameters={
            "federation_name": "fed-edge",
            "dataset_urn": "urn:li:dataset:(urn:li:dataPlatform:file,/tmp/data,PROD)",
            "python_packages": ["humanize==4.13.0", "kamiwaza-sdk==1.1.0"],
            "package_imports": ["humanize", "kamiwaza_sdk"],
            "expected_package_versions": {
                "humanize": "4.13.0",
                "kamiwaza-sdk": "1.1.0",
            },
            "initiator_cluster_id": "edge-a",
        },
    )
    return RunContext(
        selected=selected,
        params=selected.redacted_parameters,
        initiator=persona,
        receiver=object(),
        admin=SimpleNamespace(ropc_token=lambda *args: "token"),
        password="password",
        initiator_base="https://edge-a.test/api",
    )


def test_case_runs_baseline_and_delegated_exact_package_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = {
        "data": [
            {
                "classification": "U",
                "probe": "kz-delegated-base-baseline",
                "package_versions": {"humanize": None, "kamiwaza-sdk": "0.9.0"},
            }
        ],
        "metadata": {"gate_audit": [{}]},
    }
    delegated = {
        "data": [
            {
                "classification": "U",
                "probe": "kz-delegated-delegated",
                "package_imports": ["humanize", "kamiwaza_sdk"],
                "package_versions": {"humanize": "4.13.0", "kamiwaza-sdk": "1.1.0"},
            }
        ],
        "metadata": {"gate_audit": [{}]},
    }
    persona = _Persona([baseline, delegated])
    monkeypatch.setattr(
        "kamiwaza_sdk.validation.delegated_workload_cases.token_client",
        lambda base_url, token: persona,
    )
    monkeypatch.setattr(
        "kamiwaza_sdk.validation.delegated_workload_cases._issue_token",
        lambda context, username: f"token:{username}",
    )
    markers = iter((SimpleNamespace(hex="baseline"), SimpleNamespace(hex="delegated")))
    monkeypatch.setattr(
        "kamiwaza_sdk.validation.delegated_workload_cases.uuid.uuid4",
        lambda: next(markers),
    )

    results = run_edge(_run_context(persona))

    assert [result.case_id for result in results] == list(DELEGATED_CASE_IDS)
    assert [result.status for result in results] == ["passed"]
    assert persona.requests == [("GET", "/mesh/fed-edge/api/cluster/jobs/job-1/status")]
