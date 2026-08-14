from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from kamiwaza_sdk.schemas.models.model_file import ModelFile
from tests.integration.test_model_files_live import (
    TestModelFileCreateAndDelete as _TestModelFileCreateAndDelete,
)


def _client_with_created_file() -> tuple[SimpleNamespace, ModelFile]:
    created_file = ModelFile.model_validate(
        {"id": uuid4(), "name": "sdk-test-file.bin"}
    )
    models = SimpleNamespace(
        create_model_file=Mock(return_value=created_file),
        delete_model_file=Mock(),
        list_model_files=Mock(return_value=[]),
    )
    return SimpleNamespace(models=models), created_file


def test_live_model_file_cleanup_failure_is_not_swallowed() -> None:
    client, created_file = _client_with_created_file()
    client.models.delete_model_file.side_effect = RuntimeError("cleanup failed")

    with pytest.raises(RuntimeError, match="cleanup failed"):
        _TestModelFileCreateAndDelete().test_create_model_file(client)

    client.models.delete_model_file.assert_called_once_with(created_file.id)


def test_live_model_file_cleanup_verifies_row_is_absent() -> None:
    client, created_file = _client_with_created_file()
    client.models.list_model_files.return_value = [created_file]

    with pytest.raises(AssertionError):
        _TestModelFileCreateAndDelete().test_create_model_file(client)

    client.models.delete_model_file.assert_called_once_with(created_file.id)
    client.models.list_model_files.assert_called_once_with()
