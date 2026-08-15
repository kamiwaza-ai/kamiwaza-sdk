"""Deterministic neutral endpoints behind the real delegated SDK transport."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import jwt

from kamiwaza_sdk.delegated_workloads.proof import BODY_DIGEST_CLAIM, body_digest

BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
RESOURCE_URL = "https://resource.example.test/documents/doc-7"
MODEL_URL = "https://core.example.test/api/chat/completions"
RUN_ID = "11111111-1111-4111-8111-111111111111"
CLAIM_ID = "22222222-2222-4222-8222-222222222222"
MEMBER_ID = "66666666-6666-4666-8666-666666666666"
WORKLOAD_ID = "88888888-8888-4888-8888-888888888888"
CORRELATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DIGEST = "sha256:" + "d" * 64
RUN_CAPABILITY = "header.run-capability.signature"


@dataclass(frozen=True, slots=True)
class AttributionRecord:
    member_subject_id: str
    workload_actor_id: str
    member_charge_owner_id: str
    workload_budget_subject_id: str


@dataclass(frozen=True, slots=True)
class RequestView:
    method: str
    url: str
    body: bytes
    headers: Mapping[str, str]
    payload: Mapping[str, object]

    @property
    def key(self) -> tuple[str, str]:
        return self.method, self.url


class StubResponse:
    def __init__(self, body: object) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._body = body

    def json(self) -> object:
        return self._body


class NeutralPlatformSession:
    """Validate SDK wire authority and return neutral endpoint responses."""

    def __init__(self, assertion: str) -> None:
        self.assertion = assertion
        self.application_steps: list[str] = []
        self.attributions: list[AttributionRecord] = []

    def request(self, method: str, url: str, **kwargs: object) -> StubResponse:
        view = _request_view(method, url, kwargs)
        _assert_exact_proof(view)
        _assert_workload_assertion(view, self.assertion)
        step, payload = _HANDLERS[view.key](view, self.attributions)
        self.application_steps.append(step)
        return StubResponse(payload)


def _request_view(
    method: str,
    url: str,
    kwargs: Mapping[str, object],
) -> RequestView:
    body = kwargs.get("data", b"")
    headers = kwargs.get("headers", {})
    if not isinstance(body, bytes) or not isinstance(headers, Mapping):
        raise AssertionError("neutral request shape is invalid")
    payload = json.loads(body) if body else {}
    if not isinstance(payload, Mapping):
        raise AssertionError("neutral request body is invalid")
    return RequestView(method, url, body, headers, payload)


def _assert_exact_proof(view: RequestView) -> None:
    encoded = view.headers["DPoP"]
    claims = jwt.decode(encoded, options={"verify_signature": False})
    assert claims["htm"] == view.method
    assert claims["htu"] == view.url
    assert claims[BODY_DIGEST_CLAIM] == body_digest(view.body)
    authorization = view.headers.get("Authorization")
    if authorization is None:
        assert "ath" not in claims
        return
    assert claims["ath"] == _token_hash(authorization.removeprefix("DPoP "))


def _assert_workload_assertion(view: RequestView, expected: str) -> None:
    if view.key in _CAPABILITY_ONLY_KEYS:
        assert "X-Kamiwaza-Workload-Assertion" not in view.headers
        return
    assert view.headers["X-Kamiwaza-Workload-Assertion"] == expected


def _token_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _reservation(
    view: RequestView,
    records: list[AttributionRecord],
) -> tuple[str, object]:
    del view, records
    return "reserve_run", _reservation_payload()


def _claim(
    view: RequestView,
    records: list[AttributionRecord],
) -> tuple[str, object]:
    del records
    proof_header = jwt.get_unverified_header(view.headers["DPoP"])
    assert view.payload["executor_proof_jwk"] == proof_header["jwk"]
    return "claim_run", _claim_payload()


def _transition(
    view: RequestView,
    records: list[AttributionRecord],
) -> tuple[str, object]:
    del records
    transition = view.payload["transition"]
    if transition == "start":
        return "start_run", _transition_payload("running", "active")
    assert transition == "succeed"
    return "finish_run", _transition_payload("succeeded", "terminal")


def _run_read(
    view: RequestView,
    records: list[AttributionRecord],
) -> tuple[str, object]:
    del view, records
    return "read_run", _run_detail_payload()


def _effect(
    view: RequestView,
    records: list[AttributionRecord],
) -> tuple[str, object]:
    del records
    action = str(view.payload["action"])
    if action == "read":
        return "reserve_dataset_read", _effect_payload("read")
    assert action == "model.invoke"
    return "reserve_model", _effect_payload("model")


def _dataset_read(
    view: RequestView,
    records: list[AttributionRecord],
) -> tuple[str, object]:
    _record_attribution(view, records)
    return "read_dataset", {"content": "neutral document"}


def _model_invoke(
    view: RequestView,
    records: list[AttributionRecord],
) -> tuple[str, object]:
    _record_attribution(view, records)
    return "invoke_model", {"content": "neutral summary", "usage": 7}


def _record_attribution(
    view: RequestView,
    records: list[AttributionRecord],
) -> None:
    assert view.headers["Authorization"].startswith("DPoP header.effect-")
    records.append(
        AttributionRecord(MEMBER_ID, WORKLOAD_ID, MEMBER_ID, WORKLOAD_ID)
    )


def _reservation_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "status": "queued",
        "run_reference": "opaque-run-reference-0123456789abcdef",
        "correlation_id": CORRELATION_ID,
        "authority_deadline": "2026-08-10T12:00:00Z",
    }


def _claim_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "status": "claimed",
        "fencing_token": 3,
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "run_capability": RUN_CAPABILITY,
        "expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _transition_payload(run_status: str, claim_status: str) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "run_status": run_status,
        "claim_status": claim_status,
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _run_detail_payload() -> dict[str, object]:
    return {
        **_transition_payload("running", "active"),
        "grant_id": "44444444-4444-4444-8444-444444444444",
        "occurrence_key": "neutral-read-model",
        "revision_digest": DIGEST,
        "updated_at": "2026-08-09T12:00:01Z",
    }


def _effect_payload(kind: str) -> dict[str, object]:
    effect_id = {
        "read": "33333333-3333-4333-8333-333333333331",
        "model": "33333333-3333-4333-8333-333333333332",
    }[kind]
    return {
        "effect_id": effect_id,
        "decision": "allow",
        "status": "reserved",
        "policy_version": "policy-v1",
        "reason_codes": ["allowed"],
        "effect_capability": f"header.effect-{kind}-capability.signature",
        "broker_handle": None,
        "valid_until": "2026-08-09T12:01:00Z",
        "correlation_id": CORRELATION_ID,
    }


Handler = Callable[[RequestView, list[AttributionRecord]], tuple[str, object]]
_RUN_TRANSITION_URL = f"{BASE_URL}/run-claims/{CLAIM_ID}/transitions"
_RUN_READ_URL = f"{BASE_URL}/runs/{RUN_ID}"
_EFFECT_URL = f"{BASE_URL}/runs/{RUN_ID}/effects"
_CAPABILITY_ONLY_KEYS = {("POST", _EFFECT_URL)}
_HANDLERS: dict[tuple[str, str], Handler] = {
    ("POST", f"{BASE_URL}/runs"): _reservation,
    ("POST", f"{BASE_URL}/run-claims"): _claim,
    ("POST", _RUN_TRANSITION_URL): _transition,
    ("GET", _RUN_READ_URL): _run_read,
    ("POST", _EFFECT_URL): _effect,
    ("GET", RESOURCE_URL): _dataset_read,
    ("POST", MODEL_URL): _model_invoke,
}
