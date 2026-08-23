from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.integration import test_federation_trust_lifecycle_live

pytestmark = pytest.mark.unit


def test_pair_advertises_initiator_public_callback_host() -> None:
    initiator = MagicMock()
    initiator.base_url = "https://spark-2.example.com/api"
    initiator.federations.pair.return_value = SimpleNamespace(id="initiator-id")
    initiator._request.return_value = {"status": "PAIRED"}

    receiver = MagicMock()
    receiver.federations.pair.return_value = SimpleNamespace(id="receiver-id")

    fixture_fn = getattr(
        test_federation_trust_lifecycle_live.paired_federation,
        "__wrapped__",
    )
    fixture = fixture_fn(
        initiator,
        receiver,
        "https://spark-1.example.com/api",
    )
    next(fixture)
    try:
        callback_host = initiator.federations.pair.call_args.kwargs.get(
            "callback_hostname"
        )
        assert callback_host == "spark-2.example.com"
    finally:
        fixture.close()
