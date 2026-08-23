"""Regression guard for receiver-owned S3 live-test credentials (ENG-10834)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.unit

CONFTEST_PATH = Path(__file__).resolve().parents[1] / "integration" / "conftest.py"


@pytest.fixture(scope="module")
def integration_conftest():
    spec = importlib.util.spec_from_file_location(
        "_integration_conftest_s3_secret_under_test",
        CONFTEST_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_s3_catalog_secret_persists_receiver_owned_connection(
    integration_conftest,
) -> None:
    create = Mock(return_value="urn:li:secret:sdk-s3-live")
    client = Mock()
    client.get.return_value = {"username": "admin"}
    client.catalog.secrets.create = create

    secret_urn = integration_conftest._create_s3_catalog_secret(
        client,
        endpoint="http://object-store.test:9000",
        region="us-east-1",
    )

    assert secret_urn == "urn:li:secret:sdk-s3-live"
    client.get.assert_called_once_with("/auth/users/me")
    payload = create.call_args.args[0]
    assert payload.owner == "urn:li:corpuser:admin"
    assert json.loads(payload.value.get_secret_value()) == {
        "aws_access_key_id": "minioadmin",
        "aws_secret_access_key": "minioadmin",
        "endpoint_override": "http://object-store.test:9000",
        "endpoint_url": "http://object-store.test:9000",
        "region": "us-east-1",
    }
