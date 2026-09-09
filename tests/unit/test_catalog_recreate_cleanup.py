"""Pin cleanup of the global-container live regression without a live server."""

from contextlib import nullcontext
from unittest.mock import Mock, call

import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration import test_catalog_endpoints as live

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    client = Mock()
    client.workroom_scope.return_value = nullcontext(client)
    client.get.return_value = {"urn": "first"}
    return client


def test_duplicate_urns_are_both_cleaned_up(client):
    client.catalog.containers.create.side_effect = ["first", "second"]

    with pytest.raises(AssertionError):
        live.test_catalog_container_global_recreate_is_idempotent(client)

    client.delete.assert_has_calls(
        [
            call("/catalog/containers/by-urn", params={"urn": "first"}),
            call("/catalog/containers/by-urn", params={"urn": "second"}),
        ],
        any_order=True,
    )
    assert client.delete.call_count == 2


def test_same_urn_is_only_deleted_once(client):
    client.catalog.containers.create.side_effect = ["first", "first"]

    live.test_catalog_container_global_recreate_is_idempotent(client)

    client.delete.assert_called_once_with(
        "/catalog/containers/by-urn", params={"urn": "first"}
    )
    client.workroom_scope.assert_called_once_with(
        "ffffffff-ffff-ffff-ffff-ffffffffffff"
    )


def test_second_create_failure_still_cleans_first_urn(client):
    failure = APIError("create failed", status_code=409)
    client.catalog.containers.create.side_effect = ["first", failure]

    with pytest.raises(APIError, match="create failed"):
        live.test_catalog_container_global_recreate_is_idempotent(client)

    client.delete.assert_called_once_with(
        "/catalog/containers/by-urn", params={"urn": "first"}
    )


def test_cleanup_error_does_not_mask_duplicate_assertion_or_stop_cleanup(client):
    client.catalog.containers.create.side_effect = ["first", "second"]
    client.delete.side_effect = APIError("cleanup failed", status_code=500)

    with pytest.raises(AssertionError):
        live.test_catalog_container_global_recreate_is_idempotent(client)

    assert client.delete.call_count == 2


def test_first_create_failure_is_not_skipped(client):
    client.catalog.containers.create.side_effect = APIError(
        "global writer required", status_code=403
    )

    with pytest.raises(APIError, match="global writer required"):
        live.test_catalog_container_global_recreate_is_idempotent(client)

    client.delete.assert_not_called()
