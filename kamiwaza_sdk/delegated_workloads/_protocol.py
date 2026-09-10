"""Shared serialization and validation for delegated protocol clients."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from kamiwaza_sdk.delegated_workloads.errors import DelegatedProtocolError

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def validated(model: type[_ModelT], payload: object) -> _ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise DelegatedProtocolError() from exc


def json_bytes(body: Mapping[str, object]) -> bytes:
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


def base_url(value: str) -> str:
    resolved = value.rstrip("/")
    if not resolved:
        raise ValueError("delegated workload base URL is missing")
    return resolved
