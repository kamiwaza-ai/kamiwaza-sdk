"""Live SDK regression for an isolated ReBAC grant and revoke."""

from __future__ import annotations

import time
from contextlib import suppress
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.authz import (
    CheckRequest,
    ObjectModel,
    RelationshipObjectDelete,
    RelationshipTuple,
    RelationshipTupleDelete,
    SubjectModel,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _assert_denied(client, request: CheckRequest) -> None:
    """The live endpoint expresses a denied check as HTTP 403."""
    with pytest.raises(APIError) as error:
        client.authz.check_access(request)
    assert error.value.status_code == 403


def _wait_for_access(client, request: CheckRequest, *, allowed: bool) -> None:
    """Allow the server's short-lived ReBAC decision cache to expire."""
    deadline = time.monotonic() + 3
    last_observation = "no decision"
    while True:
        try:
            decision = client.authz.check_access(request)
        except APIError as error:
            if error.status_code != 403:
                raise
            last_observation = "HTTP 403"
            if not allowed:
                return
        else:
            last_observation = f"allow={decision.allow}, decision_id={decision.decision_id!r}"
            if allowed and decision.allow and decision.decision_id:
                return
        if time.monotonic() >= deadline:
            pytest.fail(f"Expected allowed={allowed}; last observed {last_observation}")
        time.sleep(0.1)


def test_rebac_grant_check_revoke_live(live_kamiwaza_client) -> None:
    """A grant allows only its subject and revocation restores denial."""
    client = live_kamiwaza_client
    object_ref = ObjectModel(namespace="dataset", id=f"sdk-rebac-{uuid4().hex}")
    allowed_subject = SubjectModel(namespace="user", id=str(uuid4()))
    other_subject = SubjectModel(namespace="user", id=str(uuid4()))
    grant = RelationshipTuple(
        subject=allowed_subject, relation="viewer", object=object_ref
    )
    allowed_check = CheckRequest(
        subject=allowed_subject, relation="viewer", object=object_ref
    )
    other_check = CheckRequest(
        subject=other_subject, relation="viewer", object=object_ref
    )

    try:
        client.authz.check_access(allowed_check)
    except APIError as error:
        if error.status_code == 404 and "rebac_disabled" in str(error):
            pytest.skip("ReBAC is disabled on this deployment")
        assert error.status_code == 403
    else:
        pytest.fail("A fresh synthetic resource unexpectedly allowed access")

    try:
        client.authz.upsert_tuple(grant)
        _wait_for_access(client, allowed_check, allowed=True)
        _assert_denied(client, other_check)

        client.authz.delete_tuple(
            RelationshipTupleDelete(
                subject=allowed_subject, relation="viewer", object=object_ref
            )
        )
        _wait_for_access(client, allowed_check, allowed=False)
    finally:
        # Best-effort: a teardown error must not replace the test failure.
        with suppress(APIError):
            client.authz.delete_object(RelationshipObjectDelete(object=object_ref))
