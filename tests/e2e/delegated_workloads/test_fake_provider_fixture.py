"""Contract for the closed fake OAuth provider used by broker journeys."""

from __future__ import annotations

import json
import pickle
from datetime import UTC, datetime, timedelta

import pytest

from .fixtures.fake_provider import (
    BrokerResourceCall,
    ClosedBrokerResource,
    ClosedOperationRejected,
    FakeOAuthProvider,
    ProviderCredentialRejected,
    ProviderResponseLost,
)

pytestmark = pytest.mark.e2e
NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
ACCESS_CANARY = "provider-access-canary-never-emit"
REVOCATION_CANARY = "provider-revocation-canary-never-emit"


def test_closed_resource_executes_named_operations_without_secret_output() -> None:
    provider, resource = _fixture()

    read = resource.execute(
        BrokerResourceCall(
            operation_id="fake.documents.get",
            resource_id="doc-7",
            params={"projection": "summary"},
        )
    )
    updated = resource.execute(
        BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
            body={"content": "updated"},
        )
    )

    assert read == {"document": {"content": "original", "id": "doc-7"}}
    assert updated == {"document": {"content": "updated", "id": "doc-7"}}
    assert [record.operation_id for record in provider.records] == [
        "fake.documents.get",
        "fake.documents.update",
    ]
    assert all(record.request_digest.startswith("sha256:") for record in provider.records)
    _assert_secret_absent(provider, resource, read, updated, provider.records)


@pytest.mark.parametrize(
    "call",
    [
        BrokerResourceCall(
            operation_id="fake.documents.delete",
            resource_id="doc-7",
        ),
        BrokerResourceCall(
            operation_id="fake.documents.get",
            resource_id="../other",
        ),
        BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
            body={"access_token": "caller-supplied"},
        ),
        BrokerResourceCall(
            operation_id="fake.documents.get",
            resource_id="doc-7",
            body={"content": "not-a-read"},
        ),
        BrokerResourceCall(
            operation_id="fake.documents.get",
            resource_id="doc-7",
            params={"projection": "full"},
        ),
        BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
            params={"projection": "summary"},
            body={"content": "updated"},
        ),
        BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
        ),
        BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
            body={"other": "updated"},
        ),
        BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
            body={"content": 7},
        ),
        BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
            body={"content": ""},
        ),
        BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
            body={"content": "x" * 4097},
        ),
    ],
)
def test_closed_resource_rejects_unknown_or_unsafe_calls_before_provider_use(
    call: BrokerResourceCall,
) -> None:
    provider, resource = _fixture()

    with pytest.raises(ClosedOperationRejected, match="operation is unavailable"):
        resource.execute(call)

    assert provider.records == ()


def test_lost_response_records_one_external_mutation_without_echoing_material() -> None:
    provider, resource = _fixture()
    provider.lose_next_response()
    call = BrokerResourceCall(
        operation_id="fake.documents.update",
        resource_id="doc-7",
        body={"content": "committed-before-loss"},
    )

    with pytest.raises(ProviderResponseLost, match="provider response unavailable") as error:
        resource.execute(call)

    assert resource.document("doc-7")["content"] == "committed-before-loss"
    assert len(provider.records) == 1
    assert provider.records[0].outcome == "response_lost"
    _assert_secret_absent(error.value, provider.records)


def test_programmatic_revocation_and_expiry_fail_closed() -> None:
    provider, resource = _fixture()

    resource.revoke()

    assert provider.revocation_acknowledged
    with pytest.raises(ProviderCredentialRejected, match="credential is unavailable"):
        resource.execute(_read_call())

    current = [NOW]
    expiring_provider = FakeOAuthProvider.seeded(
        access_credential=ACCESS_CANARY,
        revocation_handle=REVOCATION_CANARY,
        clock=lambda: current[0],
    )
    expired = ClosedBrokerResource.connect(
        expiring_provider,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=1),
    )
    current[0] = NOW + timedelta(seconds=2)
    with pytest.raises(ProviderCredentialRejected, match="credential is unavailable"):
        expired.execute(_read_call())


def test_internal_provider_lease_rejects_pickle_and_json_serialization() -> None:
    provider, resource = _fixture()

    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(resource)
    with pytest.raises(TypeError, match="cannot be serialized"):
        resource.__reduce__()
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(resource._lease)
    with pytest.raises(TypeError, match="cannot be serialized"):
        resource._lease.__reduce__()
    with pytest.raises(TypeError):
        json.dumps(resource)
    _assert_secret_absent(provider, resource)


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (NOW.replace(tzinfo=None), NOW + timedelta(minutes=1)),
        (NOW, NOW),
        (NOW, NOW + timedelta(seconds=901)),
    ],
)
def test_internal_provider_lease_requires_an_aware_bounded_window(
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    provider, _resource = _fixture()

    with pytest.raises(ValueError, match="lease window is invalid"):
        ClosedBrokerResource.connect(
            provider,
            issued_at=issued_at,
            expires_at=expires_at,
        )


def test_provider_rejects_missing_documents_and_foreign_internal_leases() -> None:
    provider, resource = _fixture()
    other_provider = FakeOAuthProvider.seeded(
        access_credential=ACCESS_CANARY + "-other",
        revocation_handle=REVOCATION_CANARY + "-other",
        clock=lambda: NOW,
    )
    other_resource = ClosedBrokerResource.connect(
        other_provider,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    with pytest.raises(ClosedOperationRejected):
        resource.document("missing")
    tampered = ClosedBrokerResource(provider, other_resource._lease)
    with pytest.raises(ProviderCredentialRejected):
        tampered.execute(_read_call())
    with pytest.raises(ProviderCredentialRejected):
        tampered.revoke()


def _fixture(
    *,
    expires_at: datetime = NOW + timedelta(minutes=5),
) -> tuple[FakeOAuthProvider, ClosedBrokerResource]:
    provider = FakeOAuthProvider.seeded(
        access_credential=ACCESS_CANARY,
        revocation_handle=REVOCATION_CANARY,
        clock=lambda: NOW,
    )
    resource = ClosedBrokerResource.connect(
        provider,
        issued_at=NOW,
        expires_at=expires_at,
    )
    return provider, resource


def _read_call() -> BrokerResourceCall:
    return BrokerResourceCall(
        operation_id="fake.documents.get",
        resource_id="doc-7",
    )


def _assert_secret_absent(*values: object) -> None:
    rendered = repr(values)
    assert ACCESS_CANARY not in rendered
    assert REVOCATION_CANARY not in rendered
