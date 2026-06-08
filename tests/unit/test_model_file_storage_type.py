"""Regression tests for ModelFile storage_type (ENG-6537).

The platform returns ``storage_type='oci'`` for OCI / ImageVolume-backed model
files. Before ENG-6537 the SDK ``StorageType`` enum only allowed
``file``/``s3``/``scratch``, so any ``Model`` whose ``m_files`` used OCI storage
failed pydantic validation — which silently skip-cascaded ~36 live SDK tests.
"""

from __future__ import annotations

import pytest

from kamiwaza_sdk.schemas.models.model_file import (
    CreateModelFile,
    ModelFile,
    StorageType,
)

pytestmark = pytest.mark.unit


def test_storage_type_accepts_oci():
    assert StorageType("oci") is StorageType.OCI
    assert str(StorageType.OCI) == "oci"


@pytest.mark.parametrize("value", ["file", "s3", "scratch", "oci"])
def test_model_file_deserializes_all_storage_types(value):
    # Mirrors the failing path: Model.m_files[].storage_type coming back from the API.
    mf = ModelFile.model_validate({"name": "weights.gguf", "storage_type": value})
    assert mf.storage_type == StorageType(value)


def test_create_model_file_accepts_oci():
    cmf = CreateModelFile.model_validate({"name": "weights.gguf", "storage_type": "oci"})
    assert cmf.storage_type is StorageType.OCI
