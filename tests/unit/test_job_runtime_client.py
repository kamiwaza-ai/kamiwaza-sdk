"""ENG-10427 — credential-free in-job Unix-socket client."""

from __future__ import annotations

import json
import socket
import struct
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Generator
from unittest.mock import patch

import pytest

from kamiwaza_sdk.exceptions import (
    DelegatedResourceNotFoundError,
    DelegationRevokedError,
    DelegationUnavailableError,
    JobIdentityUnavailableError,
)
from kamiwaza_sdk.job_runtime import JobRuntimeClient, ProcessIdentity


_HEADER = struct.Struct("!I")
_AGENT = ProcessIdentity(pid=1234, start_time_ticks=5678)


def _serve_frames(
    socket_path: Path,
    frames: Iterable[dict[str, Any]],
    received_requests: list[dict[str, Any]] | None = None,
) -> threading.Thread:
    ready = threading.Event()

    def serve() -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                listener.listen(1)
                ready.set()
                with listener.accept()[0] as connection:
                    size = _HEADER.unpack(_read_exact(connection, _HEADER.size))[0]
                    request = json.loads(_read_exact(connection, size))
                    if received_requests is not None:
                        received_requests.append(request)
                    for frame in frames:
                        payload = json.dumps(frame).encode("utf-8")
                        connection.sendall(_HEADER.pack(len(payload)) + payload)
        finally:
            socket_path.unlink(missing_ok=True)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=1)
    return thread


@pytest.fixture
def socket_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory(prefix="kz-job-", dir="/tmp") as directory:
        yield Path(directory)


def _read_exact(connection: socket.socket, size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        payload.extend(connection.recv(size - len(payload)))
    return bytes(payload)


def _write_identity(path: Path, identity: ProcessIdentity = _AGENT) -> None:
    path.write_text(
        json.dumps(
            {"pid": identity.pid, "start_time_ticks": identity.start_time_ticks}
        ),
        encoding="utf-8",
    )


def _client(socket_path: Path, identity_path: Path) -> JobRuntimeClient:
    return JobRuntimeClient(
        socket_path=socket_path,
        identity_path=identity_path,
        peer_identity_reader=lambda _connection: _AGENT,
        timeout_seconds=0.25,
    )


def test_from_environment_uses_only_socket_and_identity_paths(
    tmp_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = socket_dir / "environment.sock"
    identity_path = tmp_path / "agent.identity"
    monkeypatch.setenv("KAMIWAZA_JOB_AGENT_SOCKET", str(socket_path))
    monkeypatch.setenv("KAMIWAZA_JOB_AGENT_IDENTITY", str(identity_path))
    monkeypatch.setenv("KAMIWAZA_USER_TOKEN", "must-not-be-read")

    client = JobRuntimeClient.from_environment()

    assert client.socket_path == socket_path
    assert client.identity_path == identity_path


def test_lists_granted_datasets_after_verified_complete_stream(
    tmp_path: Path, socket_dir: Path
) -> None:
    socket_path = socket_dir / "datasets.sock"
    identity_path = tmp_path / "agent.identity"
    _write_identity(identity_path)
    frames = [
        {
            "kind": "dataset",
            "request_id": "request-1",
            "dataset_id": "urn:li:dataset:claims",
            "name": "claims",
        },
        {"kind": "complete", "request_id": "request-1", "item_count": 1},
    ]
    requests: list[dict[str, Any]] = []
    thread = _serve_frames(socket_path, frames, requests)

    with patch("uuid.uuid4") as request_uuid:
        request_uuid.return_value.hex = "request-1"
        datasets = _client(socket_path, identity_path).datasets.list_granted()

    thread.join(timeout=1)
    assert [(item.dataset_id, item.name) for item in datasets] == [
        ("urn:li:dataset:claims", "claims")
    ]
    assert requests == [
        {"version": 1, "request_id": "request-1", "operation": "datasets.list"}
    ]


def test_collects_rows_and_chat_chunks_without_exposing_capability(
    tmp_path: Path, socket_dir: Path
) -> None:
    identity_path = tmp_path / "agent.identity"
    _write_identity(identity_path)
    rows_socket = socket_dir / "rows.sock"
    rows = [
        {
            "kind": "dataset_row",
            "request_id": "rows-1",
            "dataset_urn": "urn:li:dataset:claims",
            "row": {"claim": 7},
        },
        {"kind": "complete", "request_id": "rows-1", "item_count": 1},
    ]
    requests: list[dict[str, Any]] = []
    rows_thread = _serve_frames(rows_socket, rows, requests)
    with patch("uuid.uuid4") as request_uuid:
        request_uuid.return_value.hex = "rows-1"
        result = _client(rows_socket, identity_path).retrieval.collect(
            dataset_urn="urn:li:dataset:claims"
        )
    rows_thread.join(timeout=1)
    assert result == ({"claim": 7},)
    assert requests[0]["operation"] == "datasets.retrieve"
    assert requests[0]["dataset_urn"] == "urn:li:dataset:claims"

    chat_socket = socket_dir / "chat.sock"
    chat_frames = [
        {"kind": "chat_chunk", "request_id": "chat-1", "delta": "hello "},
        {"kind": "chat_chunk", "request_id": "chat-1", "delta": "world"},
        {"kind": "complete", "request_id": "chat-1", "item_count": 2},
    ]
    chat_requests: list[dict[str, Any]] = []
    chat_thread = _serve_frames(chat_socket, chat_frames, chat_requests)
    with patch("uuid.uuid4") as request_uuid:
        request_uuid.return_value.hex = "chat-1"
        response = _client(chat_socket, identity_path).models.chat(
            deployment_id="7adcb7f4-9de0-4ee4-8cb6-73db11b3ae89",
            messages=[{"role": "user", "content": "summarize"}],
        )
    chat_thread.join(timeout=1)
    assert response == "hello world"
    assert chat_requests[0]["operation"] == "models.chat"
    assert chat_requests[0]["messages"] == [{"role": "user", "content": "summarize"}]


def test_lists_only_agent_returned_models(tmp_path: Path, socket_dir: Path) -> None:
    socket_path = socket_dir / "models.sock"
    identity_path = tmp_path / "agent.identity"
    _write_identity(identity_path)
    frames = [
        {
            "kind": "model",
            "request_id": "models-1",
            "deployment_id": "7adcb7f4-9de0-4ee4-8cb6-73db11b3ae89",
            "model_id": "6908bf4a-8086-4f9d-b625-a97be280ab1b",
            "name": "granted-model",
        },
        {"kind": "complete", "request_id": "models-1", "item_count": 1},
    ]
    thread = _serve_frames(socket_path, frames)

    with patch("uuid.uuid4") as request_uuid:
        request_uuid.return_value.hex = "models-1"
        models = _client(socket_path, identity_path).models.list_granted()

    thread.join(timeout=1)
    assert [model.name for model in models] == ["granted-model"]


def test_incomplete_stream_never_returns_success(
    tmp_path: Path, socket_dir: Path
) -> None:
    socket_path = socket_dir / "incomplete.sock"
    identity_path = tmp_path / "agent.identity"
    _write_identity(identity_path)
    thread = _serve_frames(
        socket_path,
        [
            {
                "kind": "dataset",
                "request_id": "request-1",
                "dataset_id": "urn:li:dataset:claims",
                "name": "claims",
            }
        ],
    )

    with (
        patch("uuid.uuid4") as request_uuid,
        pytest.raises(DelegationUnavailableError, match="stream completion"),
    ):
        request_uuid.return_value.hex = "request-1"
        _client(socket_path, identity_path).datasets.list_granted()
    thread.join(timeout=1)


@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        ("resource_denied", DelegatedResourceNotFoundError),
        ("delegation_revoked", DelegationRevokedError),
        ("attestation_rejected", JobIdentityUnavailableError),
    ],
)
def test_maps_closed_agent_errors(
    tmp_path: Path, socket_dir: Path, code: str, error_type: type[Exception]
) -> None:
    socket_path = socket_dir / f"{code}.sock"
    identity_path = tmp_path / "agent.identity"
    _write_identity(identity_path)
    thread = _serve_frames(
        socket_path,
        [{"kind": "error", "request_id": "request-1", "code": code}],
    )

    with (
        patch("uuid.uuid4") as request_uuid,
        pytest.raises(error_type),
    ):
        request_uuid.return_value.hex = "request-1"
        _client(socket_path, identity_path).datasets.list_granted()
    thread.join(timeout=1)


def test_agent_restart_refreshes_expected_identity_before_next_operation(
    tmp_path: Path, socket_dir: Path
) -> None:
    socket_path = socket_dir / "restart.sock"
    identity_path = tmp_path / "agent.identity"
    first = ProcessIdentity(pid=100, start_time_ticks=200)
    second = ProcessIdentity(pid=101, start_time_ticks=201)
    observed = iter((first, second))
    client = JobRuntimeClient(
        socket_path=socket_path,
        identity_path=identity_path,
        peer_identity_reader=lambda _connection: next(observed),
        timeout_seconds=0.25,
    )

    for identity in (first, second):
        _write_identity(identity_path, identity)
        thread = _serve_frames(
            socket_path,
            [{"kind": "complete", "request_id": "request-1", "item_count": 0}],
        )
        with patch("uuid.uuid4") as request_uuid:
            request_uuid.return_value.hex = "request-1"
            assert client.datasets.list_granted() == ()
        thread.join(timeout=1)


def test_identity_mismatch_sends_no_request(tmp_path: Path, socket_dir: Path) -> None:
    socket_path = socket_dir / "mismatch.sock"
    identity_path = tmp_path / "agent.identity"
    _write_identity(identity_path)
    received = bytearray()

    def serve() -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                listener.listen(1)
                ready.set()
                with listener.accept()[0] as connection:
                    connection.settimeout(0.2)
                    try:
                        received.extend(connection.recv(1))
                    except TimeoutError:
                        pass
        finally:
            socket_path.unlink(missing_ok=True)

    ready = threading.Event()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=1)
    mismatch = ProcessIdentity(pid=999, start_time_ticks=999)
    client = JobRuntimeClient(
        socket_path=socket_path,
        identity_path=identity_path,
        peer_identity_reader=lambda _connection: mismatch,
        timeout_seconds=0.05,
    )

    with pytest.raises(JobIdentityUnavailableError):
        client.datasets.list_granted()

    thread.join(timeout=1)
    assert received == b""
