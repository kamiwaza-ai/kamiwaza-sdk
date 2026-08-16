"""Technology-neutral conformance checks for the published raw HTTP fixture."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest


pytestmark = pytest.mark.contract
_ROOT = Path(__file__).parents[3]
_FIXTURE_PATH = _ROOT / "docs/delegated-workloads/conformance-v1.json"
_CLOSED_RETRY_CLASSES = {
    "never",
    "after_reauthentication",
    "nonce_required",
    "bounded_backoff",
    "idempotent_read_only",
}
_ERROR_CODES = {
    "ambiguous_effect_outcome",
    "approval_required",
    "attestation_rejected",
    "capability_expired",
    "claim_conflict",
    "credential_binding_unavailable",
    "current_authority_denied",
    "dpop_nonce_required",
    "effect_digest_conflict",
    "fenced_claim",
    "grant_inactive",
    "incompatible_contract",
    "invalid_request",
    "occurrence_digest_conflict",
    "proof_mismatch",
    "protected_resource_rejected",
    "provider_transient_failure",
    "readiness_unavailable",
    "registration_rejected",
    "replay_rejected",
    "resource_registration_rejected",
    "revision_mismatch",
    "unknown_resource_contract",
}


def test_fixture_and_harness_do_not_depend_on_the_python_sdk() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    imports = _imports(ast.parse(source))

    assert not any(name.startswith("kamiwaza_sdk") for name in imports)
    assert _fixture()["runtime_dependencies"] == [
        "HTTP client",
        "JSON parser",
        "ES256 JWS implementation",
        "SHA-256 implementation",
    ]


def test_raw_requests_are_constructible_with_the_standard_library() -> None:
    fixture = _fixture()
    base_url = "https://core.example.test" + fixture["protocol"]["base_path"]

    for exchange in fixture["http_exchanges"]:
        request = exchange["request"]
        body = _json_bytes(request["body"])
        raw = Request(
            base_url + request["path"],
            data=body,
            headers=request["headers"],
            method=request["method"],
        )
        assert raw.method == request["method"]
        assert raw.full_url.endswith(request["path"])
        assert isinstance(raw.data, bytes)
        assert json.loads(raw.data) == request["body"]


def test_proof_recipe_has_reproducible_exact_request_bindings() -> None:
    proof = _fixture()["proof_vector"]
    protected = proof["protected_request"]
    body = protected["body_utf8"].encode("utf-8")
    capability = proof["capability_ascii"].encode("ascii")

    assert proof["expected_claims"] == {
        "ath": _base64url_digest(capability),
        "https://schemas.kamiwaza.ai/dpop/body-sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
        "htm": protected["method"],
        "htu": protected["target_uri"],
    }


def test_resource_guard_vector_authorizes_then_consumes_once() -> None:
    exchanges = {item["id"]: item for item in _fixture()["http_exchanges"]}
    authorization = exchanges["authorize-protected-effect"]
    consumption = exchanges["consume-protected-effect"]
    authorization_headers = authorization["request"]["headers"]
    consumption_headers = consumption["request"]["headers"]

    assert "X-Kamiwaza-Effect-Consumption" not in authorization_headers
    assert consumption_headers["X-Kamiwaza-Effect-Consumption"] == (
        "<one-use-consumption-token>"
    )
    assert authorization["response"]["body"]["decision"] == "allow"
    assert consumption["response"]["body"]["status"] == "executing"
    assert _context(authorization) == _context(consumption)


def test_negative_guard_vectors_never_invoke_the_handler() -> None:
    cases = _fixture()["negative_guard_cases"]
    required = {
        "wrong-token-class",
        "untrusted-issuer-or-key",
        "wrong-audience-or-expired",
        "invalid-dpop-or-request-digest",
        "descriptor-revision-drift",
        "stale-claim-or-fence",
        "current-decision-deny",
        "one-use-replay",
        "spoofed-delegated-context",
    }

    assert {case["id"] for case in cases} == required
    assert all(case["expected"]["status"] == 403 for case in cases)
    assert all(not case["expected"]["handler_invoked"] for case in cases)
    assert all(
        case["expected"]["error_code"] == "protected_resource_rejected"
        for case in cases
    )


def test_closed_error_map_preserves_status_and_retry_semantics() -> None:
    mappings = {item["code"]: item for item in _fixture()["error_mapping"]}

    assert set(mappings) == _ERROR_CODES
    assert mappings["proof_mismatch"]["status"] == 401
    assert mappings["current_authority_denied"]["status"] == 403
    assert mappings["replay_rejected"]["status"] == 409
    assert mappings["unknown_resource_contract"]["status"] == 422
    assert mappings["readiness_unavailable"]["status"] == 503
    assert mappings["dpop_nonce_required"]["retry"] == "nonce_required"
    assert {item["retry"] for item in mappings.values()} <= _CLOSED_RETRY_CLASSES


def test_compatibility_metadata_forbids_silent_authority_changes() -> None:
    compatibility = _fixture()["compatibility"]
    changes = compatibility["within_v1_changes"]

    assert compatibility["current_protocol"] == "v1"
    assert compatibility["supported_protocols"] == ["v1"]
    assert compatibility["resource_guard_contracts"] == ["guard:v1"]
    assert compatibility["resource_guard_adapters"] == ["direct:v1", "asgi:v1"]
    assert changes["add_optional_response_field"] == "compatible"
    for rule in (
        "change_claim_meaning",
        "relax_guard_check",
        "add_required_field",
        "widen_default_authority",
    ):
        assert changes[rule] == "requires_new_version"
    assert compatibility["unknown_version_behavior"] == "reject_before_execution"


def _fixture() -> dict[str, Any]:
    value = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _imports(tree: ast.AST) -> set[str]:
    direct = {
        name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for name in node.names
    }
    relative = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    return direct | relative


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _base64url_digest(value: bytes) -> str:
    digest = hashlib.sha256(value).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _context(exchange: dict[str, Any]) -> object:
    return exchange["response"]["body"]["requester_context"]
