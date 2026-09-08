"""The live PSK lifecycle must prove physical and cryptographic retirement."""

from __future__ import annotations

import base64
import hashlib
import hmac
from types import SimpleNamespace

import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration import _federation_psk_rotation as rotation

pytestmark = pytest.mark.unit


class _Catalog:
    def __init__(self, state: SimpleNamespace, federation_id: str) -> None:
        self.state = state
        self.federation_id = federation_id
        self.calls = 0
        self.secrets = _Secrets(state, federation_id)

    def list_secrets(self, query: str):
        self.calls += 1
        prefix = f"clusterfed_{self.federation_id}"
        old = SimpleNamespace(name=prefix, urn=f"urn:li:dataHubSecret:{prefix}")
        suffix = "replacement" if self.state.replace_successor else "new-fingerpr"
        new_name = f"{prefix}_rot1_window_{suffix}"
        new = SimpleNamespace(name=new_name, urn=f"urn:li:dataHubSecret:{new_name}")
        assert query == prefix
        if self.state.phase == "initial":
            return [old]
        if self.state.phase == "staged":
            return [old, new]
        return [new]


class _Secrets:
    def __init__(self, state: SimpleNamespace, federation_id: str) -> None:
        self.state = state
        self.old_urn = f"urn:li:dataHubSecret:clusterfed_{federation_id}"
        self.calls = 0

    def get(self, secret_urn: str):
        self.calls += 1
        assert secret_urn == self.old_urn
        if self.state.phase != "closed" or self.state.retain_retired:
            return SimpleNamespace(urn=secret_urn)
        raise APIError("not found", status_code=404)


class _Session:
    def __init__(
        self,
        state: SimpleNamespace,
        closed_status: int = 403,
        closed_detail: str = "Invalid cluster signature",
    ) -> None:
        self.state = state
        self.closed_status = closed_status
        self.closed_detail = closed_detail
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: int):
        assert timeout == 10
        self.calls.append((url, data, headers))
        status = self.closed_status if self.state.phase == "closed" else 200
        detail = self.closed_detail if self.state.phase == "closed" else None
        return SimpleNamespace(status_code=status, json=lambda: {"detail": detail})


class _Client:
    def __init__(
        self,
        state: SimpleNamespace,
        federation_id: str,
        source_cluster_id: str,
        *,
        stale_status: int = 403,
        stale_detail: str = "Invalid cluster signature",
    ) -> None:
        self.base_url = f"https://{federation_id}.example.test/api"
        self.catalog = _Catalog(state, federation_id)
        self.session = _Session(state, stale_status, stale_detail)
        self.source_cluster_id = source_cluster_id

    def _request(self, method: str, path: str):
        assert method == "GET"
        assert path.startswith("/cluster/federations/")
        return {"remote_cluster_id": self.source_cluster_id}


def _expected_signature(body: bytes, psk: str, cluster_id: str, issued_at: int) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    payload = (
        f"body_sha256={body_hash}\n"
        f"cluster_id={cluster_id}\n"
        "method=POST\n"
        "endpoint=ping\n"
        f"iat={issued_at}"
    ).encode()
    digest = hmac.new(psk.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _pair(
    *,
    stale_status: int = 403,
    stale_detail: str = "Invalid cluster signature",
    retain_retired: bool = False,
    replace_successor: bool = False,
):
    state = SimpleNamespace(
        phase="initial",
        retain_retired=retain_retired,
        replace_successor=replace_successor,
    )
    initiator = _Client(
        state,
        "initiator-fed",
        "receiver-cluster",
        stale_status=stale_status,
        stale_detail=stale_detail,
    )
    receiver = _Client(
        state,
        "receiver-fed",
        "initiator-cluster",
        stale_status=stale_status,
        stale_detail=stale_detail,
    )
    pair = rotation.RotationPair(
        initiator=initiator,
        receiver=receiver,
        initiator_id="initiator-fed",
        receiver_id="receiver-fed",
        initial_psk="retired-k1",
    )
    return pair, state


def test_rotation_exercise_proves_catalog_cleanup_and_stale_key_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair, state = _pair()

    monkeypatch.setattr(rotation, "assert_pair_reachable", lambda _pair: None)
    monkeypatch.setattr(rotation, "_recover_lost_stage", lambda _pair: None)

    def _converge(_pair):
        state.phase = "staged"
        return "new-fingerprint", "old-fingerprint"

    def _complete(_pair, _fingerprint):
        state.phase = "closed"

    monkeypatch.setattr(rotation, "_converge_divergent_stages", _converge)
    monkeypatch.setattr(rotation, "_activate", lambda *_args: None)
    monkeypatch.setattr(rotation, "_complete", _complete)

    rotation.exercise_peer_proven_rotation(pair)

    for client in (pair.initiator, pair.receiver):
        assert client.catalog.calls == 3
        assert client.catalog.secrets.calls == 1
        assert len(client.session.calls) == 3
        for url, body, headers in client.session.calls:
            assert url == f"{client.base_url}/cluster/remote/ping"
            issued_at = int(headers["X-Cluster-Issued-At"])
            expected = _expected_signature(
                body, "retired-k1", client.source_cluster_id, issued_at
            )
            assert headers["X-Cluster-Signature"] == expected
            assert headers["X-Cluster-Id"] == client.source_cluster_id
        assert "retired-k1" not in repr(client.session.calls)


def test_rotation_exercise_fails_if_catalog_keeps_retired_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair, state = _pair(retain_retired=True)
    monkeypatch.setattr(rotation.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(rotation, "assert_pair_reachable", lambda _pair: None)
    monkeypatch.setattr(rotation, "_recover_lost_stage", lambda _pair: None)

    def _converge(_pair):
        state.phase = "staged"
        return "new-fingerprint", "old-fingerprint"

    monkeypatch.setattr(rotation, "_converge_divergent_stages", _converge)
    monkeypatch.setattr(rotation, "_activate", lambda *_args: None)

    def _complete(_pair, _fingerprint):
        state.phase = "closed"

    monkeypatch.setattr(rotation, "_complete", _complete)

    with pytest.raises(AssertionError, match="directly readable"):
        rotation.exercise_peer_proven_rotation(pair)


def test_rotation_exercise_rejects_a_different_catalog_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair, state = _pair(replace_successor=True)
    monkeypatch.setattr(rotation.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(rotation, "assert_pair_reachable", lambda _pair: None)
    monkeypatch.setattr(rotation, "_recover_lost_stage", lambda _pair: None)

    def _converge(_pair):
        state.phase = "staged"
        state.replace_successor = False
        return "new-fingerprint", "old-fingerprint"

    def _complete(_pair, _fingerprint):
        state.phase = "closed"
        state.replace_successor = True

    monkeypatch.setattr(rotation, "_converge_divergent_stages", _converge)
    monkeypatch.setattr(rotation, "_activate", lambda *_args: None)
    monkeypatch.setattr(rotation, "_complete", _complete)

    with pytest.raises(AssertionError, match="successor identity"):
        rotation.exercise_peer_proven_rotation(pair)


def test_rotation_exercise_rejects_a_persistent_wrong_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair, state = _pair(replace_successor=True)
    monkeypatch.setattr(rotation.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(rotation, "assert_pair_reachable", lambda _pair: None)
    monkeypatch.setattr(rotation, "_recover_lost_stage", lambda _pair: None)

    def _converge(_pair):
        state.phase = "staged"
        return "new-fingerprint", "old-fingerprint"

    def _complete(_pair, _fingerprint):
        state.phase = "closed"

    monkeypatch.setattr(rotation, "_converge_divergent_stages", _converge)
    monkeypatch.setattr(rotation, "_activate", lambda *_args: None)
    monkeypatch.setattr(rotation, "_complete", _complete)

    with pytest.raises(AssertionError, match="fingerprint-bound"):
        rotation.exercise_peer_proven_rotation(pair)


def test_rotation_exercise_fails_if_retired_key_is_still_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair, state = _pair(stale_status=200)
    monkeypatch.setattr(rotation, "assert_pair_reachable", lambda _pair: None)
    monkeypatch.setattr(rotation, "_recover_lost_stage", lambda _pair: None)

    def _converge(_pair):
        state.phase = "staged"
        return "new-fingerprint", "old-fingerprint"

    def _complete(_pair, _fingerprint):
        state.phase = "closed"

    monkeypatch.setattr(rotation, "_converge_divergent_stages", _converge)
    monkeypatch.setattr(rotation, "_activate", lambda *_args: None)
    monkeypatch.setattr(rotation, "_complete", _complete)

    with pytest.raises(AssertionError, match="retired K1 was not refused") as error:
        rotation.exercise_peer_proven_rotation(pair)

    assert "retired-k1" not in str(error.value)


def test_rotation_exercise_rejects_an_unrelated_forbidden_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair, state = _pair(stale_detail="Unknown cluster")
    monkeypatch.setattr(rotation, "assert_pair_reachable", lambda _pair: None)
    monkeypatch.setattr(rotation, "_recover_lost_stage", lambda _pair: None)

    def _converge(_pair):
        state.phase = "staged"
        return "new-fingerprint", "old-fingerprint"

    def _complete(_pair, _fingerprint):
        state.phase = "closed"

    monkeypatch.setattr(rotation, "_converge_divergent_stages", _converge)
    monkeypatch.setattr(rotation, "_activate", lambda *_args: None)
    monkeypatch.setattr(rotation, "_complete", _complete)

    with pytest.raises(AssertionError, match="signature validation"):
        rotation.exercise_peer_proven_rotation(pair)
