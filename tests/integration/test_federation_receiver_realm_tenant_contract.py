"""Offline contracts for receiver-realm tenant and refresh live probes."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

from kamiwaza_sdk.services.federation_credentials import FEDERATION_CREDENTIAL_HEADER
from tests.integration import conftest as integration_conftest
from tests.integration import test_federation_onboarding_clearance_gate_live as edge

pytestmark = pytest.mark.unit


def test_default_data_plane_approval_assigns_receiver_owned_canonical_tenant() -> None:
    body = edge._receiver_approval_body("urn:kamiwaza:dataset:known")

    assert body == {
        "attributes": {
            "clearance": "S",
            "tenant_id": "__default__",
        },
        "relations": [
            {
                "relation": "viewer",
                "object": "dataset:urn:kamiwaza:dataset:known",
            }
        ],
    }


def test_receiver_tenant_negative_cases_pin_readback_and_denial_contracts() -> None:
    assert [
        (
            case.case_id,
            case.approval_attributes,
            case.expected_assigned_attributes,
            case.expected_status,
            case.expected_reason,
        )
        for case in edge._TENANT_REJECTION_CASES
    ] == [
        (
            "missing-canonical",
            {"clearance": "S"},
            {"clearance": "S"},
            401,
            "tenant_required",
        ),
        (
            "legacy-only",
            {"clearance": "S", "tenant": "__default__"},
            {"clearance": "S", "tenant": "__default__"},
            401,
            "tenant_required",
        ),
        (
            "canonical-blank",
            {"clearance": "S", "tenant_id": ""},
            {"clearance": "S"},
            401,
            "tenant_required",
        ),
        (
            "canonical-whitespace-wrapped",
            {"clearance": "S", "tenant_id": " __default__ "},
            {"clearance": "S", "tenant_id": " __default__ "},
            403,
            "mesh_tenant_not_admitted",
        ),
        (
            "canonical-nondefault",
            {"clearance": "S", "tenant_id": "tenant-a"},
            {"clearance": "S", "tenant_id": "tenant-a"},
            403,
            "mesh_tenant_not_admitted",
        ),
    ]


def test_claim_identity_uses_receiver_roster_not_durable_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decode = Mock(side_effect=AssertionError("durable credential must stay opaque"))
    monkeypatch.setattr(edge, "_decode_jwt_payload", decode, raising=False)
    receiver = SimpleNamespace(
        _request=Mock(
            return_value={
                "items": [
                    {
                        "external_id": "other-sub",
                        "linked_external_user": "other-user",
                    },
                    {
                        "external_id": "guest-sub",
                        "linked_external_user": "requester-external-id",
                    },
                ]
            }
        )
    )
    state = {"receiver": receiver, "receiver_id": "receiver-federation-id"}

    identity = edge._required_claim_identity(
        state,
        {"credential": "opaque-offline-credential"},
        "requester-external-id",
    )

    assert identity == ("opaque-offline-credential", "guest-sub")
    receiver._request.assert_called_once_with(
        "GET", "/cluster/federations/receiver-federation-id/users"
    )
    decode.assert_not_called()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            {
                "external_id": "guest-sub-1",
                "linked_external_user": "requester-external-id",
            },
            {
                "external_id": "guest-sub-2",
                "linked_external_user": "requester-external-id",
            },
        ],
    ],
    ids=["missing", "ambiguous"],
)
def test_claim_identity_fails_closed_on_nonunique_receiver_roster(
    rows: list[dict[str, str]],
) -> None:
    credential = "offline-credential-canary"
    receiver = SimpleNamespace(_request=Mock(return_value={"items": rows}))
    state = {"receiver": receiver, "receiver_id": "receiver-federation-id"}

    with pytest.raises(AssertionError) as exc_info:
        edge._required_claim_identity(
            state,
            {"credential": credential},
            "requester-external-id",
        )

    assert str(exc_info.value) == (
        "receiver roster did not expose exactly one claimed guest"
    )
    assert credential not in str(exc_info.value)


def test_receiver_tenant_denial_probe_cannot_source_tenant_from_caller_header() -> None:
    response = MagicMock(status_code=401)
    response.__enter__.return_value = response
    response.json.return_value = {"detail": "tenant_required"}
    post = Mock(return_value=response)
    requester = SimpleNamespace(
        base_url="https://initiator.example/api",
        get_bearer_token=Mock(return_value="initiator-access-token"),
        session=SimpleNamespace(post=post),
    )
    path = {
        "requester": requester,
        "federation_name": "receiver edge",
        "dataset_urn": "urn:kamiwaza:dataset:known",
    }

    status, payload = edge._mesh_job_create_response(
        path,
        "receiver-offline-credential",
    )

    assert (status, payload) == (401, {"detail": "tenant_required"})
    post.assert_called_once_with(
        "https://initiator.example/api/mesh/receiver%20edge/api/retrieval/jobs",
        json={"dataset_urn": "urn:kamiwaza:dataset:known"},
        headers={
            "Authorization": "Bearer initiator-access-token",
            FEDERATION_CREDENTIAL_HEADER: "receiver-offline-credential",
            "X-Tenant-Id": "__default__",
        },
        verify=False,
        timeout=120,
    )


def test_federation_credential_install_failure_does_not_expose_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_canary = "source-offline-credential-canary"
    resolved_canary = "resolved-offline-credential-canary"
    monkeypatch.setattr(
        edge,
        "federation_credential_headers",
        Mock(return_value={FEDERATION_CREDENTIAL_HEADER: resolved_canary}),
    )
    path = {
        "credential": source_canary,
        "federation_name": "receiver-edge",
    }

    with pytest.raises(AssertionError) as exc_info:
        edge._install_federation_credential(path, monkeypatch)

    message = str(exc_info.value)
    assert message == "federation credential installation could not be verified"
    assert source_canary not in message
    assert resolved_canary not in message


def test_http_trace_writer_redacts_sensitive_headers(tmp_path: Path) -> None:
    trace_path = tmp_path / "http-trace.jsonl"
    writer = integration_conftest._HTTPTraceWriter(trace_path)
    canaries = {
        "aUtHoRiZaTiOn": "authorization-canary",
        "PROXY-authorization": "proxy-authorization-canary",
        "cookie": "cookie-canary",
        "SET-cookie": "set-cookie-canary",
        "x-kz-fEDERATION-CREDENTIAL": "federation-credential-canary",
    }

    writer.write(
        "request",
        headers=[
            ("aUtHoRiZaTiOn", canaries["aUtHoRiZaTiOn"]),
            ("PROXY-authorization", canaries["PROXY-authorization"]),
            ("cookie", canaries["cookie"]),
            ("X-Request-Id", "safe-request-id"),
        ],
    )
    writer.write(
        "response-head",
        headers=[
            ("SET-cookie", canaries["SET-cookie"]),
            (
                "x-kz-fEDERATION-CREDENTIAL",
                canaries["x-kz-fEDERATION-CREDENTIAL"],
            ),
            ("Content-Type", "application/json"),
        ],
    )

    output = trace_path.read_text(encoding="utf-8")
    for canary in canaries.values():
        assert canary not in output
    records = [json.loads(line) for line in output.splitlines()]
    request_headers = dict(records[0]["headers"])
    response_headers = dict(records[1]["headers"])
    assert request_headers == {
        "aUtHoRiZaTiOn": "[REDACTED]",
        "PROXY-authorization": "[REDACTED]",
        "cookie": "[REDACTED]",
        "X-Request-Id": "safe-request-id",
    }
    assert response_headers == {
        "SET-cookie": "[REDACTED]",
        "x-kz-fEDERATION-CREDENTIAL": "[REDACTED]",
        "Content-Type": "application/json",
    }


def _write_json_body_trace(trace_path: Path, bodies: list[dict[str, object]]) -> str:
    writer = integration_conftest._HTTPTraceWriter(trace_path)
    writer.write(
        "request",
        body=integration_conftest._trace_body_payload(json.dumps(bodies[0])),
    )
    writer.write(
        "response-body",
        streamed=False,
        body=integration_conftest._trace_body_payload(json.dumps(bodies[1])),
    )
    writer.write(
        "response-body",
        streamed=True,
        body=integration_conftest._trace_body_payload(json.dumps(bodies[2])),
    )
    return trace_path.read_text(encoding="utf-8")


def test_http_trace_writer_redacts_recursive_json_body_credentials(
    tmp_path: Path,
) -> None:
    canaries = {
        "request": "request-claim-token-canary",
        "response": "response-credential-canary",
        "nested": "nested-credential-canary",
        "stream": "stream-credential-canary",
    }
    bodies = [
        {
            "onboarding": {"ClAiM_ToKeN": canaries["request"]},
            "justification": "safe diagnostic",
        },
        {
            "credential": canaries["response"],
            "nested": [{"CREDENTIAL": canaries["nested"]}],
            "status": "APPROVED",
        },
        {"Credential": canaries["stream"], "event": "done"},
    ]

    output = _write_json_body_trace(tmp_path / "http-trace.jsonl", bodies)
    for canary in canaries.values():
        assert canary not in output
    records = [json.loads(line) for line in output.splitlines()]
    sanitized = [json.loads(record["body"]["body"]) for record in records]
    assert sanitized == [
        {
            "justification": "safe diagnostic",
            "onboarding": {"ClAiM_ToKeN": "[REDACTED]"},
        },
        {
            "credential": "[REDACTED]",
            "nested": [{"CREDENTIAL": "[REDACTED]"}],
            "status": "APPROVED",
        },
        {"Credential": "[REDACTED]", "event": "done"},
    ]
    assert [record["body"]["shape"] for record in records] == ["json"] * 3
    assert [record.get("streamed") for record in records] == [None, False, True]


def test_http_trace_writer_redacts_opaque_body_content(tmp_path: Path) -> None:
    trace_path = tmp_path / "http-trace.jsonl"
    writer = integration_conftest._HTTPTraceWriter(trace_path)
    canary = "opaque-claim-token-canary"
    raw_body = f"claim_token={canary}&diagnostic=keep"

    writer.write(
        "request",
        body=integration_conftest._trace_body_payload(
            raw_body,
            content_type="text/plain",
        ),
    )

    output = trace_path.read_text(encoding="utf-8")
    assert canary not in output
    record = json.loads(output)
    assert record["body"] == {
        "body": "[REDACTED]",
        "encoding": "utf-8",
        "shape": "opaque-text",
        "size": len(raw_body.encode("utf-8")),
    }


@pytest.mark.parametrize(
    ("case_id", "expected_message"),
    [
        (
            "unexpected-status",
            "self-service onboarding request did not enter REQUESTED state",
        ),
        (
            "missing-claim-token",
            "self-service onboarding request returned no claim token",
        ),
        (
            "missing-external-id",
            "self-service onboarding request returned no external id",
        ),
    ],
)
def test_onboarding_request_failures_do_not_expose_claim_material(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    expected_message: str,
) -> None:
    canary = "onboarding-claim-material-canary"
    status = {
        "status": "FAILED" if case_id == "unexpected-status" else "REQUESTED",
        "claim_token": None if case_id == "missing-claim-token" else canary,
        "external_id": None if case_id == "missing-external-id" else "external-id",
        "credential": canary,
    }
    monkeypatch.setattr(edge, "_self_request_onboarding", Mock(return_value=status))

    with pytest.raises(AssertionError) as exc_info:
        edge._approve_and_claim({"initiator_id": "federation-id"}, object())

    message = str(exc_info.value)
    assert message == expected_message
    assert canary not in message


def test_missing_claim_credential_failure_does_not_expose_claim_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "claim-response-material-canary"
    monkeypatch.setattr(
        edge,
        "_self_request_onboarding",
        Mock(
            return_value={
                "status": "REQUESTED",
                "claim_token": "claim-token",
                "external_id": "external-id",
            }
        ),
    )
    monkeypatch.setattr(edge, "_receiver_request_id", Mock(return_value="request-id"))
    monkeypatch.setattr(
        edge,
        "_obj",
        Mock(side_effect=[{"status": "APPROVED"}, {"status": "APPROVED"}]),
    )
    monkeypatch.setattr(
        edge,
        "_claim",
        Mock(return_value={"credential": None, "claim_token": canary}),
    )
    state = {
        "initiator_id": "initiator-id",
        "receiver_id": "receiver-id",
        "receiver": object(),
        "dataset_urn": "urn:kamiwaza:dataset:known",
    }

    with pytest.raises(AssertionError) as exc_info:
        edge._approve_and_claim(state, object())

    message = str(exc_info.value)
    assert message == "receiver claim returned no federation credential"
    assert canary not in message


def test_receiver_refresh_boundary_reuses_credential_then_revokes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = Mock()
    revoke = timeline.revoke
    revoke.return_value = {"success": True}
    by_id = Mock(return_value=SimpleNamespace(guests=SimpleNamespace(revoke=revoke)))
    path = {
        "credential": "same-offline-credential",
        "federation_name": "receiver-edge",
        "guest_sub": "guest-sub",
        "receiver": SimpleNamespace(
            federations=SimpleNamespace(by_id=by_id),
        ),
        "receiver_id": "receiver-federation-id",
    }
    decode = Mock(side_effect=AssertionError("durable credential must stay opaque"))
    install_credential = Mock()
    retrieve = timeline.retrieve
    sleep = timeline.sleep
    denial = timeline.denial
    denial.return_value = (
        403,
        {"detail": {"reason": "revoked_guest"}},
    )
    monkeypatch.setattr(edge, "_decode_jwt_payload", decode, raising=False)
    monkeypatch.setattr(edge, "_install_federation_credential", install_credential)
    monkeypatch.setattr(edge, "_assert_clearance_retrieval", retrieve)
    monkeypatch.setattr(edge, "_mesh_job_create_response", denial)

    edge._exercise_receiver_refresh_boundary(path, monkeypatch, sleeper=sleep)

    decode.assert_not_called()
    install_credential.assert_called_once_with(path, monkeypatch)
    assert retrieve.call_args_list == [call(path), call(path)]
    sleep.assert_called_once_with(edge._RECEIVER_REFRESH_BOUNDARY_WAIT_SECONDS)
    assert edge._RECEIVER_REFRESH_BOUNDARY_WAIT_SECONDS > 60
    assert timeline.mock_calls == [
        call.retrieve(path),
        call.sleep(edge._RECEIVER_REFRESH_BOUNDARY_WAIT_SECONDS),
        call.retrieve(path),
        call.revoke("guest-sub"),
        call.denial(path, "same-offline-credential"),
    ]
    by_id.assert_called_once_with("receiver-federation-id")
    revoke.assert_called_once_with("guest-sub")
    denial.assert_called_once_with(path, "same-offline-credential")
