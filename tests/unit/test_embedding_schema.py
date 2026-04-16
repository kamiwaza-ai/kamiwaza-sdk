"""Regression tests for EmbeddingInput.model_dump kwargs pass-through.

The `model_dump(**kwargs)` override added UUID-to-str coercion on top of the
Pydantic implementation. Without a guard, `data['id']` raises KeyError when
the caller passes ``exclude={'id'}`` / ``include=`` sets that omit it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from kamiwaza_sdk.schemas.embedding import EmbeddingInput

pytestmark = pytest.mark.unit


def test_model_dump_coerces_uuid_to_str_by_default():
    id_ = uuid4()
    dumped = EmbeddingInput(id=id_, text="hello").model_dump()

    assert dumped["id"] == str(id_)
    assert isinstance(dumped["id"], str)


def test_model_dump_with_exclude_id_does_not_raise():
    dumped = EmbeddingInput(id=uuid4(), text="hello").model_dump(exclude={"id"})

    assert "id" not in dumped
    assert dumped["text"] == "hello"


def test_model_dump_with_include_omitting_id_does_not_raise():
    dumped = EmbeddingInput(id=uuid4(), text="hello", max_length=64).model_dump(
        include={"text", "max_length"}
    )

    assert dumped == {"text": "hello", "max_length": 64}


def test_model_dump_with_id_none_passes_through():
    dumped = EmbeddingInput(text="hello").model_dump()

    assert dumped["id"] is None


def test_model_dump_exclude_none_does_not_raise():
    dumped = EmbeddingInput(text="hello").model_dump(exclude_none=True)

    assert "id" not in dumped
    assert dumped["text"] == "hello"


def test_model_dump_mode_json_coerces_uuid():
    id_ = uuid4()
    dumped = EmbeddingInput(id=id_, text="hello").model_dump(mode="json")

    assert dumped["id"] == str(id_)
