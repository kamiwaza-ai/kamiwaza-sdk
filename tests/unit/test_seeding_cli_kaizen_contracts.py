"""Seeder CLI tests for the Kaizen turn-contract split.

Kept out of ``test_seeding_cli.py`` so that file stays about the seeder's
general command surface and this one about the canonical/legacy divergence —
the two Kaizen products answer different routes at every step of a turn, so a
mismatch is an HTTP 422 or 404 rather than a degraded success.
"""

from __future__ import annotations

import json

import pytest

from kamiwaza_sdk.seeding import cli

from tests.unit.test_seeding_cli import FakeClient, _run

pytestmark = pytest.mark.unit

# --- canonical vs legacy Kaizen turn contract -------------------------------


def test_chat_defaults_to_the_canonical_contract(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "chat",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
            "--agent-id",
            "agent-1",
            "--message",
            "hello there",
            "--workroom-id",
            "wr-1",
        ],
        client,
    )

    # Canonical create takes no agent_id and none of the v3 body fields.
    create = client.conversations.create_canonical.calls[0]["kwargs"]
    assert create["base_url"] == "https://kamiwaza.test/kaizen"
    assert "agent_id" not in create
    assert client.conversations.create.calls == []
    # Canonical selects the agent per input, not at create.
    chat = client.conversations.chat_canonical.calls[0]
    assert chat["args"] == ("conv-2", "hello there")
    assert chat["kwargs"]["agent"] == "agent-1"
    assert chat["kwargs"]["workroom_id"] == "wr-1"
    # Nothing to wait on: canonical exposes no sandbox container_status.
    assert client.conversations.wait_until_ready.calls == []
    assert json.loads(capsys.readouterr().out) == {
        "conversation_id": "conv-2",
        "reply": "Hello from canonical.",
    }


def test_chat_legacy_extension_keeps_the_v3_turn(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "chat",
            "--extension-name",
            "kaizen-legacy",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
            "--agent-id",
            "agent-1",
            "--message",
            "hello there",
        ],
        client,
    )

    assert client.conversations.create.calls[0]["kwargs"]["agent_id"] == "agent-1"
    assert client.conversations.create_canonical.calls == []
    assert client.conversations.chat_canonical.calls == []
    assert client.conversations.chat.calls[0]["args"] == ("conv-1", "hello there")


def test_chat_unknown_extension_identity_is_rejected_locally(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit):
        _run(
            [
                "chat",
                "--extension-name",
                "kaizen-next",
                "--kaizen-base-url",
                "https://kamiwaza.test/kaizen",
                "--agent-id",
                "agent-1",
                "--message",
                "hi",
            ],
            client,
        )


def test_chat_canonical_fire_and_forget_passes_no_wait_budget(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "chat",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
            "--agent-id",
            "agent-1",
            "--message",
            "hi",
            "--timeout",
            "0",
        ],
        client,
    )

    # `0` is the CLI's fire-and-forget spelling; canonical spells it None.
    call = client.conversations.chat_canonical.calls[0]
    assert call["kwargs"]["timeout_seconds"] is None


def test_create_conversation_defaults_to_the_canonical_contract(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "create-conversation",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
        ],
        client,
    )

    assert client.conversations.create_canonical.calls
    assert client.conversations.create.calls == []
    assert json.loads(capsys.readouterr().out) == {"conversation_id": "conv-2"}


def test_create_conversation_legacy_extension_keeps_the_v3_body(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "create-conversation",
            "--extension-name",
            "kaizen-legacy",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
            "--agent-id",
            "agent-1",
            "--max-iterations",
            "12",
        ],
        client,
    )

    create = client.conversations.create.calls[0]["kwargs"]
    assert (create["agent_id"], create["max_iterations"]) == ("agent-1", 12)
    assert client.conversations.create_canonical.calls == []


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--agent-id", "agent-1"),
        ("--title", "seed convo"),
        ("--max-iterations", "12"),
    ],
)
def test_create_conversation_rejects_v3_flags_on_the_canonical_contract(
    flag, value, monkeypatch
):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    # Canonical create has no body to carry these, so honoring them is
    # impossible; dropping them silently is the failure the split exists to fix.
    with pytest.raises(SystemExit, match="does not support"):
        _run(
            [
                "create-conversation",
                "--kaizen-base-url",
                "https://kamiwaza.test/kaizen",
                flag,
                value,
            ],
            client,
        )
    assert client.conversations.create_canonical.calls == []


def test_create_conversation_rejects_ephemeral_on_the_canonical_contract(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit, match="--ephemeral"):
        _run(
            [
                "create-conversation",
                "--kaizen-base-url",
                "https://kamiwaza.test/kaizen",
                "--ephemeral",
            ],
            client,
        )


def test_create_conversation_legacy_still_requires_an_agent_id(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    # --agent-id stopped being argparse-required so canonical can reject it;
    # the legacy path must still fault when it is missing.
    with pytest.raises(SystemExit, match="--agent-id is required"):
        _run(
            [
                "create-conversation",
                "--extension-name",
                "kaizen-legacy",
                "--kaizen-base-url",
                "https://kamiwaza.test/kaizen",
            ],
            client,
        )


def test_create_conversation_legacy_defaults_max_iterations_when_unset(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "create-conversation",
            "--extension-name",
            "kaizen-legacy",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
            "--agent-id",
            "agent-1",
        ],
        client,
    )

    # The flag defaults to None so canonical can tell "operator asked" from
    # "never supplied"; the legacy body must still carry the v3 server default.
    assert client.conversations.create.calls[0]["kwargs"]["max_iterations"] == 500


def test_chat_rejects_title_on_the_canonical_contract(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    # --agent-id is legitimately used on canonical (per-input selector), but
    # --title has nowhere to go: canonical create sends no body.
    with pytest.raises(SystemExit, match="--title"):
        _run(
            [
                "chat",
                "--kaizen-base-url",
                "https://kamiwaza.test/kaizen",
                "--agent-id",
                "agent-1",
                "--message",
                "hi",
                "--title",
                "seed convo",
            ],
            client,
        )
    assert client.conversations.create_canonical.calls == []


def test_chat_legacy_still_accepts_a_title(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "chat",
            "--extension-name",
            "kaizen-legacy",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
            "--agent-id",
            "agent-1",
            "--message",
            "hi",
            "--title",
            "seed convo",
        ],
        client,
    )

    assert client.conversations.create.calls[0]["kwargs"]["title"] == "seed convo"
