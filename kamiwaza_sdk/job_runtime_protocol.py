"""Bounded wire frames for the credential-free local job-agent protocol."""

from __future__ import annotations

import socket
import struct
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from .schemas.delegated_jobs import JobChatMessage

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 64 * 1024
_HEADER = struct.Struct("!I")
_REQUEST_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class JobRuntimeProtocolError(RuntimeError):
    """The local agent sent a malformed, unsupported, or truncated frame."""


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetListRequest(_Frame):
    version: Literal[1] = 1
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    operation: Literal["datasets.list"] = "datasets.list"


class DatasetRetrieveRequest(_Frame):
    version: Literal[1] = 1
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    operation: Literal["datasets.retrieve"] = "datasets.retrieve"
    dataset_urn: str = Field(min_length=1, max_length=1024)

    @field_validator("dataset_urn")
    @classmethod
    def validate_dataset_urn(cls, value: str) -> str:
        if value != value.strip() or "*" in value:
            raise ValueError("dataset urn must be exact")
        return value


class ModelListRequest(_Frame):
    version: Literal[1] = 1
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    operation: Literal["models.list"] = "models.list"


class ModelChatRequest(_Frame):
    version: Literal[1] = 1
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    operation: Literal["models.chat"] = "models.chat"
    deployment_id: UUID
    messages: tuple[JobChatMessage, ...] = Field(min_length=1, max_length=128)


AgentRequest = (
    DatasetListRequest | DatasetRetrieveRequest | ModelListRequest | ModelChatRequest
)


class DatasetItemFrame(_Frame):
    kind: Literal["dataset"] = "dataset"
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    dataset_id: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=512)


class DatasetRowFrame(_Frame):
    kind: Literal["dataset_row"] = "dataset_row"
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    dataset_urn: str = Field(min_length=1, max_length=1024)
    row: dict[str, JsonValue]


class ModelItemFrame(_Frame):
    kind: Literal["model"] = "model"
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    deployment_id: UUID
    model_id: UUID
    name: str = Field(min_length=1, max_length=512)


class ChatChunkFrame(_Frame):
    kind: Literal["chat_chunk"] = "chat_chunk"
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    delta: str = Field(min_length=1, max_length=32 * 1024)


class CompleteFrame(_Frame):
    kind: Literal["complete"] = "complete"
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    item_count: int = Field(ge=0)


class ErrorFrame(_Frame):
    kind: Literal["error"] = "error"
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    code: Literal[
        "invalid_request",
        "job_identity_unavailable",
        "authority_unavailable",
        "operation_unavailable",
        "grant_denied",
        "delegation_revoked",
        "capability_expired",
        "attestation_rejected",
        "replay_rejected",
        "resource_denied",
    ]


AgentResponse = Annotated[
    DatasetItemFrame
    | DatasetRowFrame
    | ModelItemFrame
    | ChatChunkFrame
    | CompleteFrame
    | ErrorFrame,
    Field(discriminator="kind"),
]
_RESPONSE_ADAPTER: TypeAdapter[AgentResponse] = TypeAdapter(AgentResponse)


def send_request(connection: socket.socket, request: AgentRequest) -> None:
    payload = request.model_dump_json().encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise JobRuntimeProtocolError("job-agent request exceeds maximum size")
    connection.sendall(_HEADER.pack(len(payload)) + payload)


def receive_response(connection: socket.socket) -> AgentResponse | None:
    header = _read_exact(connection, _HEADER.size, allow_eof=True)
    if header is None:
        return None
    length = _HEADER.unpack(header)[0]
    if length <= 0 or length > MAX_FRAME_BYTES:
        raise JobRuntimeProtocolError("invalid job-agent frame length")
    payload = _read_exact(connection, length, allow_eof=False)
    if payload is None:
        raise JobRuntimeProtocolError("truncated job-agent frame")
    try:
        return cast(AgentResponse, _RESPONSE_ADAPTER.validate_json(payload))
    except ValidationError as exc:
        raise JobRuntimeProtocolError("invalid job-agent response") from exc


def _read_exact(
    connection: socket.socket, size: int, *, allow_eof: bool
) -> bytes | None:
    payload = bytearray()
    while len(payload) < size:
        chunk = connection.recv(size - len(payload))
        if not chunk:
            if allow_eof and not payload:
                return None
            raise JobRuntimeProtocolError("truncated job-agent frame")
        payload.extend(chunk)
    return bytes(payload)


__all__ = (
    "AgentRequest",
    "ChatChunkFrame",
    "CompleteFrame",
    "DatasetItemFrame",
    "DatasetListRequest",
    "DatasetRetrieveRequest",
    "DatasetRowFrame",
    "ErrorFrame",
    "JobRuntimeProtocolError",
    "ModelChatRequest",
    "ModelItemFrame",
    "ModelListRequest",
    "receive_response",
    "send_request",
)
