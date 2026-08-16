"""Credential-free client for a managed job's private local agent socket."""

from __future__ import annotations

import json
import os
import socket
import struct
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import UUID

from pydantic import ValidationError

from .exceptions import (
    DelegatedOperationDeniedError,
    DelegatedResourceNotFoundError,
    DelegationRevokedError,
    DelegationUnavailableError,
    JobIdentityUnavailableError,
)
from .job_runtime_protocol import (
    AgentRequest,
    ChatChunkFrame,
    CompleteFrame,
    DatasetItemFrame,
    DatasetListRequest,
    DatasetRetrieveRequest,
    DatasetRowFrame,
    ErrorFrame,
    JobRuntimeProtocolError,
    ModelChatRequest,
    ModelItemFrame,
    ModelListRequest,
    receive_response,
    send_request,
)
from .schemas.delegated_jobs import GrantedDataset, GrantedModel, JobChatMessage

_DEFAULT_SOCKET_PATH = "/run/kamiwaza-job-agent/private/agent.sock"
_DEFAULT_IDENTITY_PATH = "/run/kamiwaza-job-agent/private/agent.identity"
_PEER_CREDENTIALS = struct.Struct("3i")
_FrameT = TypeVar(
    "_FrameT", DatasetItemFrame, DatasetRowFrame, ModelItemFrame, ChatChunkFrame
)


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Kernel PID plus process start ticks, preventing PID-reuse confusion."""

    pid: int
    start_time_ticks: int

    def __post_init__(self) -> None:
        if self.pid <= 0 or self.start_time_ticks <= 0:
            raise ValueError("process identity values must be positive")


PeerIdentityReader = Callable[[socket.socket], ProcessIdentity]


class JobRuntimeClient:
    """Typed receiver access available only inside a managed delegated job."""

    def __init__(
        self,
        socket_path: Path,
        identity_path: Path,
        *,
        peer_identity_reader: PeerIdentityReader | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.socket_path = Path(socket_path)
        self.identity_path = Path(identity_path)
        self._peer_identity_reader = peer_identity_reader or read_linux_agent_identity
        self._timeout_seconds = timeout_seconds
        self.datasets = JobDatasets(self)
        self.retrieval = JobRetrieval(self)
        self.models = JobModels(self)

    @classmethod
    def from_environment(
        cls,
        *,
        timeout_seconds: float = 5.0,
    ) -> JobRuntimeClient:
        """Build only from platform-created local paths; never use credentials."""

        return cls(
            socket_path=Path(
                os.getenv("KAMIWAZA_JOB_AGENT_SOCKET", _DEFAULT_SOCKET_PATH)
            ),
            identity_path=Path(
                os.getenv("KAMIWAZA_JOB_AGENT_IDENTITY", _DEFAULT_IDENTITY_PATH)
            ),
            timeout_seconds=timeout_seconds,
        )

    def __enter__(self) -> JobRuntimeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Retain context-manager symmetry; operations own their sockets."""

    def _stream(
        self,
        request: AgentRequest,
        item_type: type[_FrameT],
    ) -> Iterator[_FrameT]:
        expected_identity = _read_expected_identity(self.identity_path)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self._timeout_seconds)
                connection.connect(str(self.socket_path))
                actual_identity = self._peer_identity_reader(connection)
                _require_identity(actual_identity, expected_identity)
                send_request(connection, request)
                yield from _collect_stream(connection, request.request_id, item_type)
        except (
            JobIdentityUnavailableError,
            DelegatedResourceNotFoundError,
            DelegationRevokedError,
            DelegatedOperationDeniedError,
            DelegationUnavailableError,
        ):
            raise
        except (JobRuntimeProtocolError, OSError, TimeoutError) as exc:
            raise DelegationUnavailableError(
                "job credential agent unavailable"
            ) from exc


class JobDatasets:
    """Granted dataset discovery through the job-local agent."""

    def __init__(self, client: JobRuntimeClient) -> None:
        self._client = client

    def list_granted(self) -> tuple[GrantedDataset, ...]:
        request = DatasetListRequest(request_id=uuid.uuid4().hex)
        frames = self._client._stream(request, DatasetItemFrame)
        return tuple(
            GrantedDataset(dataset_id=frame.dataset_id, name=frame.name)
            for frame in frames
        )


class JobRetrieval:
    """Exact dataset retrieval through the job-local agent."""

    def __init__(self, client: JobRuntimeClient) -> None:
        self._client = client

    def stream(self, *, dataset_urn: str) -> Iterator[dict[str, Any]]:
        request = DatasetRetrieveRequest(
            request_id=uuid.uuid4().hex,
            dataset_urn=dataset_urn,
        )
        for frame in self._client._stream(request, DatasetRowFrame):
            if frame.dataset_urn != dataset_urn:
                raise DelegationUnavailableError("agent dataset identity mismatch")
            yield dict(frame.row)

    def collect(self, *, dataset_urn: str) -> tuple[dict[str, Any], ...]:
        return tuple(self.stream(dataset_urn=dataset_urn))


class JobModels:
    """Granted model discovery and chat through the job-local agent."""

    def __init__(self, client: JobRuntimeClient) -> None:
        self._client = client

    def list_granted(self) -> tuple[GrantedModel, ...]:
        request = ModelListRequest(request_id=uuid.uuid4().hex)
        frames = self._client._stream(request, ModelItemFrame)
        return tuple(
            GrantedModel(
                deployment_id=frame.deployment_id,
                model_id=frame.model_id,
                name=frame.name,
            )
            for frame in frames
        )

    def stream_chat(
        self,
        *,
        deployment_id: str,
        messages: Iterable[JobChatMessage | Mapping[str, Any]],
    ) -> Iterator[str]:
        request = ModelChatRequest(
            request_id=uuid.uuid4().hex,
            deployment_id=UUID(deployment_id),
            messages=tuple(_chat_message(message) for message in messages),
        )
        for frame in self._client._stream(request, ChatChunkFrame):
            yield frame.delta

    def chat(
        self,
        *,
        deployment_id: str,
        messages: Iterable[JobChatMessage | Mapping[str, Any]],
    ) -> str:
        return "".join(self.stream_chat(deployment_id=deployment_id, messages=messages))


def read_linux_agent_identity(connection: socket.socket) -> ProcessIdentity:
    peer_option = getattr(socket, "SO_PEERCRED", None)
    if peer_option is None:
        raise JobIdentityUnavailableError("credential agent identity is unavailable")
    raw = connection.getsockopt(socket.SOL_SOCKET, peer_option, _PEER_CREDENTIALS.size)
    pid, _uid, _gid = _PEER_CREDENTIALS.unpack(raw)
    return _read_process_identity(pid)


def _read_process_identity(pid: int) -> ProcessIdentity:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        identity_text, separator, tail = stat_text.rstrip().rpartition(")")
        stat_pid, command_separator, _command = identity_text.partition("(")
        if not separator or not command_separator or int(stat_pid.strip()) != pid:
            raise ValueError("invalid process stat")
        return ProcessIdentity(pid=pid, start_time_ticks=int(tail.split()[19]))
    except (IndexError, OSError, ValueError) as exc:
        raise JobIdentityUnavailableError(
            "credential agent identity is unavailable"
        ) from exc


def _read_expected_identity(path: Path) -> ProcessIdentity:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProcessIdentity(
            pid=int(payload["pid"]),
            start_time_ticks=int(payload["start_time_ticks"]),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JobIdentityUnavailableError(
            "credential agent identity is unavailable"
        ) from exc


def _require_identity(actual: ProcessIdentity, expected: ProcessIdentity) -> None:
    if actual != expected:
        raise JobIdentityUnavailableError("credential agent identity mismatch")


def _collect_stream(
    connection: socket.socket,
    request_id: str,
    item_type: type[_FrameT],
) -> Iterator[_FrameT]:
    count = 0
    while True:
        frame = receive_response(connection)
        if frame is None:
            raise DelegationUnavailableError("agent closed before stream completion")
        if frame.request_id != request_id:
            raise DelegationUnavailableError("agent response request id mismatch")
        if isinstance(frame, item_type):
            count += 1
            yield cast(_FrameT, frame)
            continue
        if isinstance(frame, ErrorFrame):
            raise _error_for_code(frame.code)
        if isinstance(frame, CompleteFrame):
            if frame.item_count != count:
                raise DelegationUnavailableError("agent response item count mismatch")
            return
        raise DelegationUnavailableError("agent returned unexpected response type")


def _error_for_code(code: str) -> Exception:
    if code in {"grant_denied", "resource_denied"}:
        return DelegatedResourceNotFoundError("delegated resource not found")
    if code == "delegation_revoked":
        return DelegationRevokedError("delegated job authority was revoked")
    if code == "operation_unavailable":
        return DelegatedOperationDeniedError("delegated operation is unavailable")
    if code in {"job_identity_unavailable", "attestation_rejected"}:
        return JobIdentityUnavailableError("job identity is unavailable")
    return DelegationUnavailableError("delegated authority is unavailable")


def _chat_message(value: JobChatMessage | Mapping[str, Any]) -> JobChatMessage:
    if isinstance(value, JobChatMessage):
        return value
    try:
        return JobChatMessage.model_validate(value)
    except ValidationError:
        raise


__all__ = (
    "JobRuntimeClient",
    "ProcessIdentity",
)
