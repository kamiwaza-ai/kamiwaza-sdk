"""Regression tests for the workroom bundle export workflow.

These drive a real :class:`~kamiwaza_sdk.client.KamiwazaClient` with
``requests.Session.send`` replaced, so the service method body and the
streaming branch inside it are the ones under test. A service-level fake hides
exactly the defect these cover: ``WorkroomService.export_bundle`` returns
``response.content`` when it is given no path, and the workflow used to publish
``str()`` of those bytes.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import requests

from kamiwaza_sdk.agent_tools.workflows._contract import WORKFLOWS, Refusal
from kamiwaza_sdk.agent_tools.workflows.collaboration import export_workroom_bundle
from kamiwaza_sdk.schemas.workrooms import ExportManifest

pytestmark = pytest.mark.unit

WORKROOM_ID = "8b1f4bd0-0000-4000-8000-00000000000c"
#: A real ZIP end-of-central-directory record: 22 bytes, and the smallest
#: payload whose bytes repr is unmistakable in a published string.
BUNDLE = b"PK\x05\x06" + b"\x00" * 18
_EXPORT_DIR_ENV = "KAMIWAZA_AGENT_EXPORT_DIR"


def _manifest_payload() -> dict[str, Any]:
    """Build the manifest body the platform returns for an export."""
    return {
        "workroom_id": WORKROOM_ID,
        "items": [{"type": "dataset", "name": "sales", "exportable": True}],
    }


def _response(request: Any, body: bytes, content_type: str) -> requests.Response:
    """Build the response ``requests`` would hand back for ``request``."""
    response = requests.Response()
    response.status_code = 200
    response.url = request.url
    response.request = request
    response.headers["Content-Type"] = content_type
    # _content_consumed makes iter_content re-slice _content, which is the
    # branch WorkroomService._write_response_stream walks when it streams.
    response._content = body
    response._content_consumed = True
    return response


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real client whose transport serves the export and the manifest."""
    for name in ("KAMIWAZA_API_KEY", "KAMIWAZA_API_TOKEN", _EXPORT_DIR_ENV):
        monkeypatch.delenv(name, raising=False)
    sent: list[tuple[str, str]] = []

    def fake_send(self: Any, request: Any, **kwargs: Any) -> requests.Response:
        sent.append((request.method, request.url))
        if request.url.endswith("/export"):
            return _response(request, BUNDLE, "application/zip")
        if request.url.endswith("/export/manifest"):
            import json

            return _response(
                request, json.dumps(_manifest_payload()).encode(), "application/json"
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    monkeypatch.setattr(requests.Session, "send", fake_send)
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        built = KamiwazaClient(base_url="http://localhost:7777/api")
    built.sent_requests = sent  # type: ignore[attr-defined]
    return built


def test_export_with_no_path_writes_a_file_and_publishes_no_bundle_bytes(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No path used to mean ``export_bundle`` returned the ZIP itself.

    The workflow then published ``str(bytes)``, so the archive travelled as a
    bytes repr at two to four times its own size and no location came back.
    """
    monkeypatch.setenv(_EXPORT_DIR_ENV, str(tmp_path))

    result = export_workroom_bundle(client, WORKROOM_ID)

    assert not isinstance(result, Refusal)
    written = Path(result["location"])
    assert written.parent == tmp_path.resolve()
    assert written.read_bytes() == BUNDLE
    assert result["size_bytes"] == len(BUNDLE)
    published = repr(result)
    assert "PK" not in published
    assert "\\x05\\x06" not in published
    assert isinstance(result["manifest"], ExportManifest)
    assert result["manifest"].workroom_id == UUID(WORKROOM_ID)
    assert client.sent_requests[0][0] == "POST"


def test_export_refuses_an_absolute_path_outside_the_export_directory(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An agent-named absolute path opened "wb" writes any writable file."""
    allowed = tmp_path / "exports"
    allowed.mkdir()
    outside = tmp_path / "authorized_keys"
    monkeypatch.setenv(_EXPORT_DIR_ENV, str(allowed))

    result = export_workroom_bundle(client, WORKROOM_ID, output_path=str(outside))

    assert isinstance(result, Refusal)
    assert str(allowed) in result.shortfall
    assert not outside.exists()
    assert client.sent_requests == [], "refused, so nothing was exported"


def test_export_refuses_a_traversal_out_of_the_export_directory(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``..`` is refused rather than stripped, so the caller learns it failed."""
    allowed = tmp_path / "exports"
    allowed.mkdir()
    monkeypatch.setenv(_EXPORT_DIR_ENV, str(allowed))

    result = export_workroom_bundle(client, WORKROOM_ID, output_path="../escaped.zip")

    assert isinstance(result, Refusal)
    assert not (tmp_path / "escaped.zip").exists()
    assert client.sent_requests == []


def test_export_refuses_a_symlink_that_leaves_the_export_directory(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Containment is checked after resolution, so a symlink cannot smuggle."""
    allowed = tmp_path / "exports"
    allowed.mkdir()
    (allowed / "elsewhere").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setenv(_EXPORT_DIR_ENV, str(allowed))

    result = export_workroom_bundle(
        client, WORKROOM_ID, output_path="elsewhere/escaped.zip"
    )

    assert isinstance(result, Refusal)
    assert not (tmp_path / "escaped.zip").exists()
    assert client.sent_requests == []


def test_export_refuses_when_the_host_configured_no_directory(client: Any) -> None:
    """With nowhere approved to write, the workflow declines to invent one."""
    result = export_workroom_bundle(client, WORKROOM_ID)

    assert isinstance(result, Refusal)
    assert _EXPORT_DIR_ENV in result.shortfall
    assert client.sent_requests == []


def test_the_export_approval_names_what_is_written_and_where() -> None:
    """The approval sentence is all an approver reads before saying yes."""
    approval = WORKFLOWS["export_workroom_bundle"].approval_step

    assert approval is not None
    assert "bundle" in approval
    assert "export directory" in approval


def test_the_export_declares_the_non_idempotence_its_calls_derive() -> None:
    """``workrooms.export_bundle`` derives ``idempotent=False`` from its verb.

    ``envelopes`` publishes ``idempotent`` as ``safe_to_retry``, so declaring
    ``True`` over a derived ``False`` tells a host a retry is free. It is not:
    the platform emits an export audit event per call
    (kamiwaza/services/workrooms/api.py:3938) and this workflow rewrites the
    destination file.
    """
    spec = WORKFLOWS["export_workroom_bundle"]

    assert spec.idempotent is False
    assert spec.not_idempotent_because
    assert "retry" in spec.not_idempotent_because
