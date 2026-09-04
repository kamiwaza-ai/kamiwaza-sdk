"""ENG-10050: Offline contract for the required shared-IDP smoke edge."""

import ast
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from _kamiwaza_pytest_options import PROJECT_ROOT
from kamiwaza_sdk.exceptions import APIError, AuthenticationError
from tests.integration import test_federation_shared_idp_gated_retrieval_live as edge

try:
    from tests.integration import required_federation_edge as required_edge
    from tests.integration import required_federation_edge_setup as required_setup
except ImportError:  # exact-parent RED: the required-edge plugin does not exist
    required_edge = SimpleNamespace()
    required_setup = SimpleNamespace()

pytestmark = pytest.mark.unit


def _required_item(nodeid: str) -> SimpleNamespace:
    config = SimpleNamespace(getoption=lambda name: name == "require_federation_edge")
    return SimpleNamespace(
        config=config,
        fspath=Path(required_edge.REQUIRED_EDGE_FILE),
        keywords={
            "requires_two_clusters": True,
            "requires_shared_idp": True,
            "requires_owned_shared_realm": True,
        },
        nodeid=nodeid,
    )


def _assert_live_retrieval_parametrization() -> None:
    retrieval_marks = [
        mark
        for mark in edge.test_required_mesh_retrieval_returns_exact_post_gate_rows.pytestmark
        if mark.name == "parametrize"
    ]
    assert len(retrieval_marks) == 1
    assert retrieval_marks[0].args == ("clearance", ["U", "S", "TS"])
    assert retrieval_marks[0].kwargs == {}


def _assert_live_tenant_rejection_parametrization() -> None:
    rejection_marks = [
        mark
        for mark in edge.test_required_mesh_retrieval_rejects_invalid_tenant.pytestmark
        if mark.name == "parametrize"
    ]
    assert len(rejection_marks) == 1
    assert rejection_marks[0].args[0] == "case"
    assert rejection_marks[0].kwargs == {}
    assert [
        (parameter.id, parameter.values[0]) for parameter in rejection_marks[0].args[1]
    ] == [(case.persona_key, case) for case in edge._TENANT_REJECTION_CASES]


def test_required_edge_carries_owned_shared_realm_capability_markers() -> None:
    marker_names = {marker.name for marker in edge.pytestmark}

    assert {
        "requires_two_clusters",
        "requires_shared_idp",
        "requires_owned_shared_realm",
    } <= marker_names


def test_required_edge_plugin_is_registered_only_at_pytest_root() -> None:
    root_conftest = (PROJECT_ROOT / "conftest.py").read_text()
    integration_conftest = (PROJECT_ROOT / "tests/integration/conftest.py").read_text()

    assert "tests.integration.required_federation_edge" in root_conftest
    assert "pytest_plugins" not in integration_conftest


def test_required_edge_collection_guard_requires_all_nine_cases() -> None:
    expected_cases = {
        "test_required_mesh_retrieval_returns_exact_post_gate_rows[U]",
        "test_required_mesh_retrieval_returns_exact_post_gate_rows[S]",
        "test_required_mesh_retrieval_returns_exact_post_gate_rows[TS]",
        "test_required_mesh_retrieval_rejects_invalid_tenant[missing-canonical]",
        "test_required_mesh_retrieval_rejects_invalid_tenant[legacy-only]",
        "test_required_mesh_retrieval_rejects_invalid_tenant[canonical-nondefault]",
        "test_required_mesh_dataset_list_returns_only_authorized_fixture",
        "test_required_mesh_job_reaches_receiver_and_returns_marker",
        "test_unonboarded_shared_idp_user_rejected_by_receiver_allowlist",
    }
    assert required_edge.REQUIRED_EDGE_CASES == expected_cases
    assert len(required_edge.REQUIRED_EDGE_CASES) == 9
    _assert_live_retrieval_parametrization()
    _assert_live_tenant_rejection_parametrization()
    for case in expected_cases:
        function_name = case.split("[", 1)[0]
        assert callable(getattr(edge, function_name, None)), case
    items = [
        _required_item(f"tests/integration/{required_edge.REQUIRED_EDGE_FILE}::{case}")
        for case in required_edge.REQUIRED_EDGE_CASES
    ]

    required_edge.assert_required_cases(items)

    with pytest.raises(pytest.UsageError, match="missing contract cases"):
        required_edge.assert_required_cases(items[:-1])


def test_required_edge_operator_guidance_names_all_nine_cases() -> None:
    options = (PROJECT_ROOT / "_kamiwaza_pytest_options.py").read_text()
    matrix = (
        PROJECT_ROOT / "tests/integration/federation_1_2_uat_coverage_matrix.md"
    ).read_text()

    option_literals = {
        node.value
        for node in ast.walk(ast.parse(options))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert any("all nine contract cases" in value for value in option_literals)
    assert "nine-case shared-IDP edge" in matrix
    assert "six-case shared-IDP edge" not in matrix


def test_required_edge_collection_guard_inspects_live_parametrization(
    monkeypatch,
) -> None:
    retrieval = edge.test_required_mesh_retrieval_returns_exact_post_gate_rows
    monkeypatch.setattr(retrieval, "pytestmark", [])

    with pytest.raises(AssertionError):
        _assert_live_retrieval_parametrization()


def test_required_edge_post_selection_guard_rejects_deselection_and_extras() -> None:
    items = [
        _required_item(f"tests/integration/{required_edge.REQUIRED_EDGE_FILE}::{case}")
        for case in required_edge.REQUIRED_EDGE_CASES
    ]

    required_edge.assert_selected_required_cases(items)

    with pytest.raises(pytest.UsageError, match="selected contract cases"):
        required_edge.assert_selected_required_cases(items[:-1])

    extra = _required_item(
        f"tests/integration/{required_edge.REQUIRED_EDGE_FILE}::test_optional_case"
    )
    with pytest.raises(pytest.UsageError, match="selected contract cases"):
        required_edge.assert_selected_required_cases([*items, extra])


def test_required_edge_promotes_any_skip_to_failure() -> None:
    item = _required_item(
        "tests/integration/"
        f"{required_edge.REQUIRED_EDGE_FILE}::"
        "test_required_mesh_job_reaches_receiver_and_returns_marker"
    )
    report = SimpleNamespace(
        longrepr="missing shared realm",
        outcome="skipped",
        skipped=True,
        when="setup",
    )

    required_edge.promote_skip(item, report)

    assert report.outcome == "failed"
    assert "missing shared realm" in report.longrepr

    unrelated = _required_item(
        "tests/integration/test_unrelated_live.py::test_optional_edge"
    )
    unrelated.fspath = Path("test_unrelated_live.py")
    optional_report = SimpleNamespace(
        longrepr="optional capability",
        outcome="skipped",
        skipped=True,
        when="setup",
    )
    required_edge.promote_skip(unrelated, optional_report)
    assert optional_report.outcome == "skipped"


def test_required_prerequisite_fails_in_strict_lane() -> None:
    strict = SimpleNamespace(getoption=lambda name: name == "require_federation_edge")
    optional = SimpleNamespace(getoption=lambda name: False)

    with pytest.raises(pytest.fail.Exception, match="missing shared realm"):
        edge._require_prerequisite(strict, False, "missing shared realm")
    with pytest.raises(pytest.skip.Exception, match="missing shared realm"):
        edge._require_prerequisite(optional, False, "missing shared realm")


def test_required_mesh_call_propagates_downstream_denial() -> None:
    denial = APIError("receiver denied", status_code=403)

    def deny() -> None:
        raise denial

    with pytest.raises(APIError) as caught:
        edge._required_mesh_call(deny)

    assert caught.value is denial


def test_unonboarded_shared_user_uses_mesh_and_rejects_generic_denial() -> None:
    client = Mock()
    client._request.side_effect = APIError("forbidden", status_code=403)

    with pytest.raises(AssertionError, match="unauthorized_brokered_user"):
        edge.test_unonboarded_shared_idp_user_rejected_by_receiver_allowlist(
            {
                "name": "receiver-cluster",
                "personas": {
                    "unonboarded": {
                        "client": client,
                        "authenticator": Mock(
                            get_access_token=Mock(return_value="shared-token")
                        ),
                    }
                },
            }
        )

    client._request.assert_called_once_with(
        "GET",
        "/mesh/receiver-cluster/api/cluster/diagnose",
    )


def test_unonboarded_shared_user_accepts_receiver_allowlist_rejection() -> None:
    denial = APIError(
        "not onboarded",
        status_code=403,
        response_data={
            "detail": {"reason": "unauthorized_brokered_user"},
        },
    )

    edge._assert_receiver_onboarding_rejection(Mock(side_effect=denial))


@pytest.mark.parametrize(
    "denial",
    [
        AuthenticationError("unauthorized"),
        APIError(
            "peer rejected",
            status_code=403,
            response_data={
                "detail": {"reason": "shared_idp_issuer_untrusted"},
            },
        ),
    ],
)
def test_unonboarded_shared_user_rejects_unrelated_auth_failure(denial) -> None:
    with pytest.raises(AssertionError, match="unauthorized_brokered_user"):
        edge._assert_receiver_onboarding_rejection(Mock(side_effect=denial))


def test_brokered_personas_get_only_required_fixture_authority() -> None:
    dataset_tuple = {
        "subject": "user:{{user_id}}",
        "relation": "viewer",
        "object": "dataset:urn:kamiwaza:dataset:known",
    }
    assert edge._required_initial_tuples(
        "urn:kamiwaza:dataset:known",
        job_executor=False,
    ) == [dataset_tuple]
    assert edge._required_initial_tuples(
        "urn:kamiwaza:dataset:known",
        job_executor=True,
    ) == [
        dataset_tuple,
        {
            "subject": "user:{{user_id}}",
            "relation": "executor",
            "object": "cluster_jobs:__all__",
        },
    ]


def test_persona_session_performs_password_grant_then_real_refresh(monkeypatch) -> None:
    calls: list[str] = []

    class _Authenticator:
        def __init__(self, config, *, token_store) -> None:
            self.config = config
            self.token_store = token_store

        def authenticate(self, session) -> None:
            calls.append("password")
            session.headers["Authorization"] = "Bearer initial"
            self.token_store.save(SimpleNamespace(refresh_token="refresh-1"))

        def refresh_token(self, session) -> None:
            calls.append("refresh")
            session.headers["Authorization"] = "Bearer refreshed"
            self.token_store.save(SimpleNamespace(refresh_token="refresh-2"))

        def get_access_token(self, session) -> str:
            calls.append("read")
            return session.headers["Authorization"].removeprefix("Bearer ")

    class _Client:
        def __init__(
            self, *, base_url, authenticator, verify, owns_authenticator
        ) -> None:
            self.base_url = base_url
            self.authenticator = authenticator
            self.verify = verify
            self.owns_authenticator = owns_authenticator
            self.session = SimpleNamespace(headers={})

    monkeypatch.setattr(edge, "SharedIdpAuthenticator", _Authenticator, raising=False)
    monkeypatch.setattr(edge, "KamiwazaClient", _Client, raising=False)
    monkeypatch.setattr(
        edge,
        "decode_jwt_payload",
        Mock(side_effect=AssertionError("raw session must not inspect claims")),
    )

    persona = edge._programmatic_persona_session(
        "https://initiator.example/api",
        {
            "issuer": "https://idp.example/realms/shared",
            "client_id": "federation-client",
            "client_secret": None,
            "password": "secret",
            "idp_verify": "/tmp/shared-idp-ca.pem",
            "platform_verify": True,
            "allow_insecure_tls": False,
        },
        "fed-clr-u",
    )

    assert calls == ["password", "refresh", "read"]
    assert persona["token"] == "refreshed"
    assert persona["client"].authenticator is persona["authenticator"]
    assert persona["authenticator"].config.verify == "/tmp/shared-idp-ca.pem"
    assert persona["client"].verify is True
    assert persona["client"].owns_authenticator is True


def test_shared_ca_pem_uses_a_dedicated_idp_bundle(tmp_path: Path) -> None:
    ca_pem = "-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n"

    verify = edge._shared_idp_verify(
        {"shared_ca_pem": ca_pem},
        platform_verify=True,
        temp_root=tmp_path,
    )

    ca_path = Path(verify)
    assert ca_path.read_text() == ca_pem
    assert stat.S_IMODE(ca_path.stat().st_mode) == 0o600
    assert (
        edge._shared_idp_verify({}, platform_verify=False, temp_root=tmp_path) is False
    )


@pytest.mark.parametrize(
    "previous", [None, SimpleNamespace(type="old.Gate", config={})]
)
def test_temporary_execution_gate_restores_receiver_binding(previous) -> None:
    cluster = Mock()
    receiver = SimpleNamespace(cluster=cluster)
    monkeypatch_current = Mock(return_value=previous)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(edge, "_current_execution_gate", monkeypatch_current)
        with edge._temporary_execution_gate(receiver):
            cluster.set_execution_gate.assert_called_once_with(
                type=edge._ALLOW_ALL_EXECUTION_GATE,
                config={},
            )

    if previous is None:
        cluster.clear_execution_gate.assert_called_once_with()
    else:
        assert cluster.set_execution_gate.call_args_list[-1].kwargs == {
            "type": "old.Gate",
            "config": {},
        }


def test_temporary_execution_gate_restores_after_ambiguous_set_failure() -> None:
    previous = SimpleNamespace(type="old.Gate", config={"mode": "strict"})
    cluster = Mock()
    cluster.set_execution_gate.side_effect = [RuntimeError("response lost"), None]
    receiver = SimpleNamespace(cluster=cluster)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            edge, "_current_execution_gate", Mock(return_value=previous)
        )
        with pytest.raises(RuntimeError, match="response lost"):
            with edge._temporary_execution_gate(receiver):
                pytest.fail("ambiguous setup must not yield")

    assert [call.kwargs for call in cluster.set_execution_gate.call_args_list] == [
        {"type": edge._ALLOW_ALL_EXECUTION_GATE, "config": {}},
        {"type": "old.Gate", "config": {"mode": "strict"}},
    ]


def test_terminal_job_oracle_accepts_exact_receiver_marker() -> None:
    result = SimpleNamespace(
        status="SUCCEEDED",
        result={"probe": "eng10050-exact"},
    )

    edge._assert_terminal_mesh_job(result, "eng10050-exact")


def test_required_dataset_list_uses_mesh_and_requires_exact_fixture() -> None:
    list_datasets = Mock(
        return_value=[SimpleNamespace(urn="urn:dataset:only-authorized")]
    )
    persona = SimpleNamespace(
        catalog=SimpleNamespace(
            datasets=SimpleNamespace(list=list_datasets),
        ),
        session=object(),
    )
    authenticator = Mock()
    authenticator.get_access_token.return_value = "opaque-test-token"
    wiring = {
        "name": "receiver cluster",
        "urn": "urn:dataset:only-authorized",
        "personas": {
            "U": {"client": persona, "authenticator": authenticator},
        },
    }

    edge.test_required_mesh_dataset_list_returns_only_authorized_fixture(wiring)

    list_datasets.assert_called_once_with(target_cluster="receiver cluster")


def test_required_job_case_runs_recoverably_on_named_peer(monkeypatch) -> None:
    jobs = Mock()
    jobs.run.return_value = SimpleNamespace(
        job_id="job-on-receiver",
        status="SUCCEEDED",
        result={"probe": "eng10050-fixed"},
    )
    request = Mock(
        return_value={
            "id": "job-on-receiver",
            "source": "mesh",
            "source_cluster_id": "initiator-uuid",
        }
    )
    persona = SimpleNamespace(jobs=jobs, _request=request, session=object())
    authenticator = Mock()
    authenticator.get_access_token.return_value = "opaque-test-token"
    monkeypatch.setattr(edge.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    wiring = {
        "name": "receiver-cluster",
        "personas": {
            "U": {"client": persona, "authenticator": authenticator},
        },
        "verify": True,
        "source_cluster_id": "initiator-uuid",
        "receiver_cluster_id": "receiver-uuid",
    }

    edge.test_required_mesh_job_reaches_receiver_and_returns_marker(
        wiring,
    )

    kwargs = jobs.run.call_args.kwargs
    assert kwargs["target_cluster"] == "receiver-cluster"
    assert kwargs["recoverable"] is True
    assert "eng10050-fixed" in kwargs["entrypoint"]
    request.assert_called_once_with(
        "GET",
        "/mesh/receiver-cluster/api/cluster/jobs/job-on-receiver/status",
    )


def _write_gate_wheel(tmp_path: Path) -> str:
    wheel = tmp_path / edge.mc.WHEEL_NAME
    wheel.write_bytes(b"exact-wheel")
    return str(tmp_path)


def _gate_package(wheel_dir: str, **overrides) -> SimpleNamespace:
    state = {
        "name": "acme-gates",
        "package_spec": edge.mc.PACKAGE_SPEC,
        "version": "1.1.0",
        "hash_digest": edge.mc._wheel_sha256(wheel_dir),
        "status": "active",
        "classpaths": [edge.mc.GATE_CLASSPATH],
    }
    state.update(overrides)
    return SimpleNamespace(**state)


def _gate_receiver(package: SimpleNamespace | None) -> SimpleNamespace:
    packages = Mock()
    if package is None:
        packages.get.side_effect = APIError("not found", status_code=404)
    else:
        packages.get.return_value = package
    gates = SimpleNamespace(
        packages=packages,
        discover=Mock(return_value=SimpleNamespace(name=edge.mc.GATE_NAME)),
    )
    return SimpleNamespace(gates=gates)


def test_preexisting_gate_package_must_match_and_is_never_owned(tmp_path) -> None:
    wheel_dir = _write_gate_wheel(tmp_path)
    receiver = _gate_receiver(_gate_package(wheel_dir))
    cleanup = Mock()

    required_setup._ensure_gate_package(cleanup, receiver, wheel_dir, "index")
    receiver.gates.packages.install.assert_not_called()
    cleanup.callback.assert_not_called()

    receiver = _gate_receiver(_gate_package(wheel_dir, version="9.9.9"))
    with pytest.raises(AssertionError):
        required_setup._ensure_gate_package(cleanup, receiver, wheel_dir, "index")
    receiver.gates.packages.install.assert_not_called()


def test_gate_package_read_or_install_failure_never_registers_cleanup(
    tmp_path, monkeypatch
) -> None:
    wheel_dir = _write_gate_wheel(tmp_path)
    receiver = _gate_receiver(None)
    receiver.gates.packages.install.side_effect = RuntimeError("install failed")
    receiver.cluster = Mock()
    cleanup = Mock()
    prerequisites = SimpleNamespace(
        wheel_dir=wheel_dir,
        index_url="index",
        dataset_path="/fixture.csv",
    )
    monkeypatch.setattr(edge.mc, "declare_clearance_attribute", Mock())

    with pytest.raises(RuntimeError, match="install failed"):
        required_setup.provision_gated_dataset(
            cleanup,
            receiver,
            prerequisites,
            "edge",
        )
    cleanup.callback.assert_not_called()

    receiver.gates.packages.get.side_effect = APIError("read failed", status_code=500)
    with pytest.raises(APIError, match="read failed"):
        required_setup._ensure_gate_package(
            cleanup,
            receiver,
            wheel_dir,
            "index",
        )


@pytest.mark.parametrize("failure", ["metadata", "discover"])
def test_owned_gate_package_cleanup_is_registered_before_post_install_failure(
    tmp_path,
    failure,
) -> None:
    wheel_dir = _write_gate_wheel(tmp_path)
    receiver = _gate_receiver(None)
    package = _gate_package(wheel_dir)
    receiver.gates.packages.install.return_value = SimpleNamespace(package=package)
    if failure == "metadata":
        package.hash_digest = "sha256:wrong"
    else:
        receiver.gates.discover.side_effect = RuntimeError("discover failed")
    cleanup = Mock()

    with pytest.raises((AssertionError, RuntimeError)):
        required_setup._ensure_gate_package(
            cleanup,
            receiver,
            wheel_dir,
            "index",
        )

    cleanup.callback.assert_called_once_with(
        required_setup._uninstall_owned_gate_package,
        receiver,
    )


def test_pairing_requires_two_distinct_cluster_identities() -> None:
    cleanup = Mock()
    request = SimpleNamespace(
        name="edge",
        psk="secret",
        peer_url="https://peer.example/api",
        shared={"identity_mode": "shared_idp"},
    )
    initiator_federations = Mock()
    initiator_federations.pair.return_value = SimpleNamespace(id="initiator-fed")
    receiver_federations = Mock()
    receiver_federations.pair.return_value = SimpleNamespace(id="receiver-fed")
    clients = SimpleNamespace(
        initiator=SimpleNamespace(federations=initiator_federations),
        receiver=SimpleNamespace(federations=receiver_federations),
    )
    receiver_federations.get.return_value = SimpleNamespace(
        remote_cluster_id="initiator-cluster"
    )
    initiator_federations.get.return_value = SimpleNamespace(
        remote_cluster_id="receiver-cluster"
    )

    identities = required_setup.pair_required_edge(cleanup, clients, request)
    assert identities.initiator_cluster_id == "initiator-cluster"
    assert identities.receiver_cluster_id == "receiver-cluster"

    initiator_federations.get.return_value = SimpleNamespace(
        remote_cluster_id="initiator-cluster"
    )
    with pytest.raises(AssertionError, match="one cluster identity"):
        required_setup.pair_required_edge(cleanup, clients, request)


def test_required_retrieval_case_asserts_streamed_known_answer(monkeypatch) -> None:
    persona = SimpleNamespace(session=object())
    authenticator = Mock()
    authenticator.get_access_token.return_value = "opaque-test-token"
    rows = edge.mc.records()[:3]
    footers = [
        {
            "filtered": True,
            "included": None,
            "redacted": None,
            "total": None,
            "gate": None,
        }
    ]
    retrieve = Mock(return_value=(rows, footers))
    monkeypatch.setattr(edge.mc, "mesh_retrieve_through_gate", retrieve)
    wiring = {
        "name": "receiver-cluster",
        "urn": "urn:kamiwaza:dataset:known",
        "personas": {
            "U": {"client": persona, "authenticator": authenticator},
        },
        "verify": True,
    }

    edge.test_required_mesh_retrieval_returns_exact_post_gate_rows(
        "U",
        wiring,
        SimpleNamespace(base_url="https://initiator.example/api"),
    )

    assert retrieve.call_args.args == (
        persona,
        "https://initiator.example/api",
        "opaque-test-token",
        "receiver-cluster",
        "urn:kamiwaza:dataset:known",
    )


def _tenant_rejection_wiring(post: Mock, persona_key: str) -> dict:
    persona = SimpleNamespace(session=SimpleNamespace(post=post))
    authenticator = Mock(get_access_token=Mock(return_value="opaque-test-token"))
    return {
        "name": "receiver-cluster",
        "urn": "urn:kamiwaza:dataset:known",
        "personas": {
            persona_key: {
                "client": persona,
                "authenticator": authenticator,
            }
        },
        "verify": True,
    }


@pytest.mark.parametrize(
    "case",
    edge._TENANT_REJECTION_CASES,
    ids=lambda case: case.persona_key,
)
def test_required_tenant_rejection_case_asserts_exact_producer_denial(
    monkeypatch,
    case,
) -> None:
    response = MagicMock(status_code=case.expected_status)
    response.__enter__.return_value = response
    response.json.return_value = {"detail": case.expected_reason}
    post = Mock(return_value=response)
    monkeypatch.setattr(edge, "federation_credential_headers", Mock(return_value={}))
    wiring = _tenant_rejection_wiring(post, case.persona_key)

    edge.test_required_mesh_retrieval_rejects_invalid_tenant(
        case,
        wiring,
        SimpleNamespace(base_url="https://initiator.example/api"),
    )

    post.assert_called_once_with(
        "https://initiator.example/api/mesh/receiver-cluster/api/retrieval/jobs",
        json={"dataset_urn": "urn:kamiwaza:dataset:known"},
        headers={"Authorization": "Bearer opaque-test-token"},
        verify=True,
        timeout=120,
    )


def test_required_tenant_rejection_oracle_rejects_wrong_reason(monkeypatch) -> None:
    case = edge._TenantRejectionCase(
        "missing-canonical",
        401,
        "tenant_required",
    )
    response = MagicMock(status_code=401)
    response.__enter__.return_value = response
    response.json.return_value = {"detail": "wrong_reason"}
    post = Mock(return_value=response)
    monkeypatch.setattr(edge, "federation_credential_headers", Mock(return_value={}))
    wiring = _tenant_rejection_wiring(post, case.persona_key)

    with pytest.raises(AssertionError):
        edge.test_required_mesh_retrieval_rejects_invalid_tenant(
            case,
            wiring,
            SimpleNamespace(base_url="https://initiator.example/api"),
        )


def test_required_tenant_rejection_oracle_rejects_non_json(monkeypatch) -> None:
    case = edge._TenantRejectionCase("missing-canonical", 401, "tenant_required")
    response = MagicMock(status_code=401)
    response.__enter__.return_value = response
    response.json.side_effect = ValueError("not JSON")
    monkeypatch.setattr(edge, "federation_credential_headers", Mock(return_value={}))
    wiring = _tenant_rejection_wiring(Mock(return_value=response), case.persona_key)

    with pytest.raises(pytest.fail.Exception, match="non-JSON body"):
        edge.test_required_mesh_retrieval_rejects_invalid_tenant(
            case,
            wiring,
            SimpleNamespace(base_url="https://initiator.example/api"),
        )


def test_required_tenant_rejection_oracle_propagates_transport_error(
    monkeypatch,
) -> None:
    case = edge._TenantRejectionCase("missing-canonical", 401, "tenant_required")
    post = Mock(side_effect=RuntimeError("transport failed"))
    monkeypatch.setattr(edge, "federation_credential_headers", Mock(return_value={}))
    wiring = _tenant_rejection_wiring(post, case.persona_key)

    with pytest.raises(RuntimeError, match="transport failed"):
        edge.test_required_mesh_retrieval_rejects_invalid_tenant(
            case,
            wiring,
            SimpleNamespace(base_url="https://initiator.example/api"),
        )


def test_exact_retrieval_oracle_rejects_duplicate_allowed_rows() -> None:
    duplicate_rows = [edge.mc.records()[0]] * 3
    footer = {
        "filtered": True,
        "included": None,
        "redacted": None,
        "total": None,
        "gate": None,
    }

    with pytest.raises(AssertionError, match="wrong post-gate rows"):
        edge.mc.assert_persona_result("U", duplicate_rows, [footer])


def test_persona_cleanup_revokes_exact_allowlist_row() -> None:
    request = Mock(return_value={"message": "revoked"})
    receiver = SimpleNamespace(_request=request)

    edge._cleanup_brokered_persona(
        receiver,
        "federation-id",
        "alice@example.com@source-cluster",
    )

    request.assert_called_once_with(
        "POST",
        "/cluster/federations/federation-id/users/"
        "alice%40example.com%40source-cluster/revoke",
        params={"cancel_in_flight_jobs": "true"},
    )


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(status="FAILED", result={"probe": "eng10050-exact"}),
        SimpleNamespace(status="SUCCEEDED", result={"probe": "wrong"}),
        SimpleNamespace(status="SUCCEEDED", result=None),
    ],
)
def test_terminal_job_oracle_rejects_false_green_result(result) -> None:
    with pytest.raises(AssertionError):
        edge._assert_terminal_mesh_job(result, "eng10050-exact")
