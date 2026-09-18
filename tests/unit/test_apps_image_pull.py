from __future__ import annotations

from uuid import uuid4
import warnings

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.services.apps import AppService

pytestmark = pytest.mark.unit


class PullClient:
    def __init__(self, reply: dict):
        self.reply = reply
        self.path: str | None = None

    def post(self, path: str) -> dict:
        self.path = path
        return self.reply


def _service(reply: dict) -> tuple[AppService, PullClient]:
    client = PullClient(reply)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return AppService(client), client


def test_pull_images_accepts_121_empty_template_response() -> None:
    template_id = uuid4()
    service, client = _service({"message": "No images to pull", "images": []})

    result = service.pull_images(template_id)

    assert client.path == f"/apps/images/pull/{template_id}"
    assert result.template_id == template_id
    assert result.total_images == result.successful_pulls == 0
    assert result.results == []
    assert result.all_successful is True


def test_pull_images_rejects_unrecognized_response() -> None:
    service, _ = _service({"message": "Pull failed", "images": []})

    with pytest.raises(ValidationError):
        service.pull_images(uuid4())
