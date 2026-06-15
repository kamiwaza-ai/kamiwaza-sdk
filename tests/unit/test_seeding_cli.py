from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kamiwaza_sdk.seeding import cli

pytestmark = pytest.mark.unit


class RecordingService:
    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self._result


class FakeClient:
    """A client whose service methods record their calls."""

    def __init__(self):
        self.auth = SimpleNamespace(
            login_with_password=RecordingService(
                SimpleNamespace(access_token="tok-123")
            )
        )
        self.workrooms = SimpleNamespace(
            create=RecordingService(SimpleNamespace(id="wr-1"))
        )
        self.models = SimpleNamespace(
            register_external_model=RecordingService(SimpleNamespace(id="model-1"))
        )
        self.apps = SimpleNamespace(
            install_by_name=RecordingService(SimpleNamespace(id="dep-1", name="kaizen"))
        )
        self.agents = SimpleNamespace(
            create=RecordingService(SimpleNamespace(id="agent-1"))
        )
        self.conversations = SimpleNamespace(
            create=RecordingService(SimpleNamespace(id="conv-1"))
        )
        self.skills = SimpleNamespace(
            import_skill_package=RecordingService(SimpleNamespace(id="skill-1"))
        )
        self.connectors = SimpleNamespace(
            create_m365=RecordingService(SimpleNamespace(id="conn-1", name="Microsoft 365"))
        )


def _run(argv, client):
    rc = cli.main(argv, client_factory=lambda **_kw: client)
    return rc


def test_login_json_output_reads_password_from_env(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("ADMIN_PW", "s3cret")

    _run(["login", "--username", "admin", "--password-env", "ADMIN_PW"], client)

    call = client.auth.login_with_password.calls[0]
    # Username is positional; the password comes from the env var, not argv.
    assert call["args"] == ("admin", "s3cret")
    assert json.loads(capsys.readouterr().out) == {"access_token": "tok-123"}


def test_login_raw_prints_bare_token(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("ADMIN_PW", "s3cret")

    _run(["login", "--password-env", "ADMIN_PW", "--raw"], client)

    # --raw emits just the token (no JSON, no trailing structure) for $(...) capture.
    assert capsys.readouterr().out.strip() == "tok-123"
    # Username defaults to "admin".
    assert client.auth.login_with_password.calls[0]["args"][0] == "admin"


def test_login_empty_password_env_exits(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("ADMIN_PW", "")

    with pytest.raises(SystemExit):
        _run(["login", "--password-env", "ADMIN_PW"], client)

    # Fail closed: an empty password must never reach the login call.
    assert client.auth.login_with_password.calls == []


def test_login_missing_password_env_arg_exits():
    # --password-env is required; omitting it must fail rather than prompt.
    with pytest.raises(SystemExit):
        _run(["login", "--username", "admin"], FakeClient())


def test_login_rejects_password_on_argv():
    # The password must never be accepted from argv — only via --password-env.
    with pytest.raises(SystemExit):
        _run(["login", "--password", "s3cret"], FakeClient())


def test_create_workroom_count_suffixes_names(capsys):
    client = FakeClient()

    _run(["create-workroom", "--name", "uat", "--count", "2"], client)

    calls = client.workrooms.create.calls
    assert [c["kwargs"]["name"] for c in calls] == ["uat-1", "uat-2"]
    out = json.loads(capsys.readouterr().out)
    assert out == {"workroom_ids": ["wr-1", "wr-1"]}


def test_register_external_model_bedrock_reads_credential_env(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setenv(
        "AWS_CRED",
        '{"auth_type":"iam","aws_access_key_id":"AK","aws_secret_access_key":"s"}',
    )

    _run(
        [
            "register-external-model",
            "--protocol",
            "aws_bedrock",
            "--name",
            "claude",
            "--region",
            "us-east-1",
            "--model-id",
            "anthropic.claude-3-sonnet-20240229-v1:0",
            "--credential-env",
            "AWS_CRED",
        ],
        client,
    )

    call = client.models.register_external_model.calls[0]["kwargs"]
    assert call["endpoint"].protocol == "aws_bedrock"
    assert call["endpoint"].model_id == "anthropic.claude-3-sonnet-20240229-v1:0"
    assert (
        call["credential"]
        == '{"auth_type":"iam","aws_access_key_id":"AK","aws_secret_access_key":"s"}'
    )
    assert call["force_replace_credentials"] is False
    assert json.loads(capsys.readouterr().out) == {"model_id": "model-1"}


def test_register_external_model_missing_credential_exits(monkeypatch):
    client = FakeClient()
    with pytest.raises(SystemExit):
        _run(
            [
                "register-external-model",
                "--protocol",
                "aws_transcribe",
                "--name",
                "t",
                "--region",
                "us-west-2",
                "--s3-bucket",
                "b",
            ],
            client,
        )


def test_install_extension_passes_workroom_and_sync(capsys, monkeypatch):
    client = FakeClient()
    scoped_calls: list = []
    monkeypatch.setattr(
        cli,
        "scoped_client_for_workroom",
        lambda c, wid: scoped_calls.append(wid) or c,
    )

    _run(
        ["install-extension", "--name", "kaizen", "--workroom-id", "wr-9", "--no-sync"],
        client,
    )

    # Workroom-scoped install enters the workroom for a scoped token.
    assert scoped_calls == ["wr-9"]
    call = client.apps.install_by_name.calls[0]
    assert call["args"] == ("kaizen",)
    assert call["kwargs"]["workroom_id"] == "wr-9"
    assert call["kwargs"]["sync_if_missing"] is False
    assert json.loads(capsys.readouterr().out) == {
        "deployment_id": "dep-1",
        "name": "kaizen",
    }


def test_create_agent_uses_kaizen_base_url(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "create-agent",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
            "--name",
            "seed-agent",
            "--model",
            "llama-3",
            "--provider",
            "kamiwaza",
            "--endpoint-path",
            "/dep/abc",
            "--workroom-id",
            "wr-1",
        ],
        client,
    )

    call = client.agents.create.calls[0]["kwargs"]
    assert call["base_url"] == "https://kamiwaza.test/kaizen"
    assert call["llm"].model == "llama-3"
    assert call["workroom_id"] == "wr-1"
    assert json.loads(capsys.readouterr().out) == {"agent_id": "agent-1"}


def test_create_agent_missing_llm_api_key_env_exits(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)
    monkeypatch.delenv("MISSING_KEY_VAR", raising=False)

    with pytest.raises(SystemExit):
        _run(
            [
                "create-agent",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
                "--model", "m",
                "--llm-api-key-env", "MISSING_KEY_VAR",
            ],
            client,
        )


def test_create_conversation(capsys):
    client = FakeClient()

    _run(
        [
            "create-conversation",
            "--kaizen-base-url",
            "https://kamiwaza.test/kaizen",
            "--agent-id",
            "agent-1",
        ],
        client,
    )

    call = client.conversations.create.calls[0]["kwargs"]
    assert call["base_url"] == "https://kamiwaza.test/kaizen"
    assert call["agent_id"] == "agent-1"
    assert json.loads(capsys.readouterr().out) == {"conversation_id": "conv-1"}


def test_import_skill_reads_file(capsys, tmp_path):
    client = FakeClient()
    pkg = tmp_path / "my-skill.zip"
    pkg.write_bytes(b"PK\x03\x04zip-bytes")

    _run(["import-skill", "--file", str(pkg)], client)

    call = client.skills.import_skill_package.calls[0]["kwargs"]
    assert call["filename"] == "my-skill.zip"
    assert call["file_content"] == b"PK\x03\x04zip-bytes"
    assert json.loads(capsys.readouterr().out) == {"skill_id": "skill-1"}


def test_configure_m365_passes_tenant_and_client(capsys):
    client = FakeClient()

    _run(
        ["configure-m365", "--tenant-id", "tenant-abc", "--client-id", "client-xyz"],
        client,
    )

    call = client.connectors.create_m365.calls[0]["kwargs"]
    assert call["tenant_id"] == "tenant-abc"
    assert call["client_id"] == "client-xyz"
    assert call["scopes"] is None  # default set applied in the service
    assert json.loads(capsys.readouterr().out) == {
        "connector_id": "conn-1",
        "name": "Microsoft 365",
    }


def test_no_subcommand_errors():
    with pytest.raises(SystemExit):
        cli.main([], client_factory=lambda **_kw: FakeClient())


def test_parse_env_rejects_bad_pair():
    with pytest.raises(SystemExit):
        cli._parse_env(["NOTAVALIDPAIR"])
