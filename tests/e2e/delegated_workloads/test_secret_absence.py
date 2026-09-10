"""Seeded provider canary scan across every delegated consumer surface."""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import zlib
from dataclasses import asdict, dataclass
from urllib.parse import quote

import pytest

from kamiwaza_sdk.delegated_workloads import (
    CredentialBindingUnavailable,
    CredentialUseResponse,
    OpaqueRunQueuePayload,
    ReplayRejected,
    TrustedAdapterLease,
)

from .brokered_effect_harness import (
    PROVIDER_ACCESS_CANARY,
    PROVIDER_REVOCATION_CANARY,
    BrokerJourneyHarness,
)

pytestmark = pytest.mark.e2e
_LOGGER = logging.getLogger("delegated-workload-secret-scan")
_REQUIRED_SURFACES = {
    "audits",
    "database",
    "environment",
    "errors",
    "exports",
    "logs",
    "model_requests",
    "prompts",
    "queue",
    "traces",
}


class SecretCanaryFound(AssertionError):
    """A seeded provider value crossed the broker boundary."""


@dataclass(frozen=True, slots=True)
class _ScanInputs:
    harness: BrokerJourneyHarness
    receipt: CredentialUseResponse
    replay: Exception
    revoked: Exception
    logs: str


def _canary_forms(canary: str) -> set[str]:
    encoded = canary.encode()
    return {
        canary,
        base64.b64encode(encoded).decode(),
        base64.urlsafe_b64encode(encoded).decode(),
        encoded.hex(),
        quote(canary, safe=""),
        base64.b64encode(gzip.compress(encoded, mtime=0)).decode(),
        base64.b64encode(zlib.compress(encoded)).decode(),
    }


def test_seeded_provider_values_are_absent_from_every_consumer_surface(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = BrokerJourneyHarness.create()
    effect_request, use_request = harness.read_requests("secret-scan:doc-7")
    effect = harness.executor.reserve_effect(effect_request, harness.run_authority)
    lease = TrustedAdapterLease.from_effect(effect, use_request)
    receipt = harness.broker.execute(lease)
    replay = _captured_error(ReplayRejected, harness.broker.execute, lease)
    revoked = _revoked_error()

    with caplog.at_level(logging.INFO, logger=_LOGGER.name):
        _LOGGER.info("broker receipt=%r", receipt)
        _LOGGER.info("broker events=%r", harness.safe_events)
        _LOGGER.info("broker errors=%r", (replay, revoked))

    surfaces = _surface_corpus(
        _ScanInputs(harness, receipt, replay, revoked, caplog.text)
    )

    assert set(surfaces) == _REQUIRED_SURFACES
    for name, value in surfaces.items():
        _assert_secret_absent(name, value)


@pytest.mark.parametrize("encoded", sorted(_canary_forms(PROVIDER_ACCESS_CANARY)))
def test_secret_scanner_negative_control_detects_every_supported_encoding(
    encoded: str,
) -> None:
    with pytest.raises(SecretCanaryFound, match="negative_control"):
        _assert_secret_absent("negative_control", {"value": encoded})


def _revoked_error() -> CredentialBindingUnavailable:
    harness = BrokerJourneyHarness.create()
    effect_request, use_request = harness.read_requests("secret-scan-revoked:doc-7")
    effect = harness.executor.reserve_effect(effect_request, harness.run_authority)
    harness.revoke_binding(effect_request.credential_binding_id)
    lease = TrustedAdapterLease.from_effect(effect, use_request)
    return _captured_error(CredentialBindingUnavailable, harness.broker.execute, lease)


def _captured_error(expected, operation, value):
    with pytest.raises(expected) as caught:
        operation(value)
    return caught.value


def _surface_corpus(inputs: _ScanInputs) -> dict[str, object]:
    events = [asdict(event) for event in inputs.harness.safe_events]
    records = [asdict(record) for record in inputs.harness.provider.records]
    receipt_body = inputs.receipt.model_dump(mode="json")
    errors = [_safe_error(inputs.replay), _safe_error(inputs.revoked)]
    export = {"events": events, "provider_records": records, "receipt": receipt_body}
    return {
        "database": events,
        "queue": OpaqueRunQueuePayload(
            run_reference="opaque-run-reference-0123456789abcdef"
        ).model_dump(mode="json"),
        "environment": dict(os.environ),
        "prompts": [{"role": "tool", "content": receipt_body["result"]}],
        "model_requests": {"messages": [{"content": receipt_body["result"]}]},
        "logs": inputs.logs,
        "traces": _trace_rows(events),
        "audits": events,
        "exports": json.dumps(export, default=str, sort_keys=True),
        "errors": errors,
    }


def _trace_rows(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "binding_id": event["binding_id"],
            "effect_id": event["effect_id"],
            "lease_id": event["lease_id"],
            "status": event["status"],
        }
        for event in events
    ]


def _safe_error(error: Exception) -> dict[str, object]:
    return {
        "message": str(error),
        "representation": repr(error),
        "body": getattr(error, "body", None),
    }


def _assert_secret_absent(name: str, value: object) -> None:
    rendered = json.dumps(value, default=str, sort_keys=True)
    for canary in (PROVIDER_ACCESS_CANARY, PROVIDER_REVOCATION_CANARY):
        if any(encoded in rendered for encoded in _canary_forms(canary)):
            raise SecretCanaryFound(f"{name}: provider credential canary found")
