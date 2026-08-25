from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kamiwaza_sdk.exceptions import DeploymentFailedError, KamiwazaError
from kamiwaza_sdk.seeding import cli

pytestmark = pytest.mark.unit


def _raiser(exc):
    def _f(*_args, **_kwargs):
        raise exc

    return _f


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
            register_external_model=RecordingService(SimpleNamespace(id="model-1")),
            list_models=RecordingService(
                [SimpleNamespace(id="model-1", name="bedrock-uat")]
            ),
        )
        self.serving = SimpleNamespace(
            deploy_model=RecordingService("dep-xyz"),
            list_active_deployments=RecordingService(
                [
                    SimpleNamespace(
                        id="dep-xyz",
                        endpoint="https://host/runtime/models/dep-xyz/v1",
                    )
                ]
            ),
        )
        self.apps = SimpleNamespace(
            install_by_name=RecordingService(SimpleNamespace(id="dep-1", name="kaizen"))
        )
        self.agents = SimpleNamespace(
            create=RecordingService(SimpleNamespace(id="agent-1")),
            create_canonical=RecordingService(SimpleNamespace(id="agent-1", version=1)),
        )
        self.kaizen_ops = SimpleNamespace(
            set_chat_model=RecordingService({"chat": {"current": {"id": "dep-xyz"}}})
        )
        self.conversations = SimpleNamespace(
            create=RecordingService(SimpleNamespace(id="conv-1")),
            create_canonical=RecordingService(SimpleNamespace(id="conv-2")),
            wait_until_ready=RecordingService(SimpleNamespace(id="conv-1")),
            chat=RecordingService("Hello! I am claude."),
            chat_canonical=RecordingService("Hello from canonical."),
        )
        self.skills = SimpleNamespace(
            import_skill_package=RecordingService(SimpleNamespace(id="skill-1"))
        )
        self.connectors = SimpleNamespace(
            create=RecordingService(SimpleNamespace(id="conn-1", name="Microsoft 365"))
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


def test_login_rejects_password_on_argv(monkeypatch):
    # The password must never be accepted from argv. argparse abbreviation is
    # disabled, so `--password` is an unrecognized flag (not a prefix alias for
    # `--password-env`) and parsing fails before any login is attempted — even
    # if a same-named env var happens to exist.
    client = FakeClient()
    monkeypatch.setenv("s3cret", "leaked")  # would be picked up if `--password` aliased

    with pytest.raises(SystemExit):
        _run(["login", "--password", "s3cret"], client)

    assert client.auth.login_with_password.calls == []


def test_secret_env_flags_reject_abbreviation(monkeypatch):
    # Sibling sweep: the other secret-bearing subcommands disable abbreviation too,
    # so `--credential` / `--llm-api-key` can't alias their `-env` forms.
    monkeypatch.setenv("AWS_CRED", '{"auth_type":"iam"}')
    monkeypatch.setenv("LLM_KEY", "sk-leaked")
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            ["register-external-model", "--protocol", "aws_bedrock", "--name", "n",
             "--region", "r", "--model-id", "m", "--credential", "AWS_CRED"]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["create-agent", "--kaizen-base-url", "u", "--name", "n", "--model", "m",
             "--llm-api-key", "LLM_KEY"]
        )


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


def test_resolve_kaizen_url_scopes_and_matches_workroom(capsys, monkeypatch):
    client = FakeClient()
    scoped_calls: list = []
    monkeypatch.setattr(
        cli,
        "scoped_client_for_workroom",
        lambda c, wid: scoped_calls.append(wid) or c,
    )
    # Capture how resolution is invoked: the wait must be told BOTH the base name
    # and the workroom id, so it matches the right workroom's Kaizen — not any.
    seen: dict = {}

    def fake_wait(_client, name, *, workroom_id, **_kw):
        seen.update(name=name, workroom_id=workroom_id)
        return "https://k/kaizen"

    monkeypatch.setattr(cli, "wait_for_base_url", fake_wait)

    _run(["resolve-kaizen-url", "--workroom-id", "wr-9"], client)

    assert scoped_calls == ["wr-9"]
    assert seen == {"name": "kaizen", "workroom_id": "wr-9"}
    assert json.loads(capsys.readouterr().out) == {
        "kaizen_base_url": "https://k/kaizen"
    }


def test_resolve_kaizen_url_raw_prints_bare_url(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)
    monkeypatch.setattr(cli, "wait_for_base_url", lambda *a, **k: "https://k/kaizen")

    _run(["resolve-kaizen-url", "--workroom-id", "wr-9", "--raw"], client)

    # --raw emits just the URL for $(...) capture — no JSON envelope.
    assert capsys.readouterr().out.strip() == "https://k/kaizen"


def test_resolve_kaizen_url_timeout_exits_nonzero(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    def time_out(*_a, **_k):
        raise TimeoutError("ingress not resolvable")

    monkeypatch.setattr(cli, "wait_for_base_url", time_out)

    # A readiness timeout surfaces as a non-zero exit with the wait's message.
    with pytest.raises(SystemExit):
        _run(["resolve-kaizen-url", "--workroom-id", "wr-9"], client)


def test_resolve_kaizen_url_ambiguous_exits_nonzero(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    def ambiguous(*_a, **_k):
        raise cli.AmbiguousExtensionError("Multiple 'kaizen' extensions in workroom")

    monkeypatch.setattr(cli, "wait_for_base_url", ambiguous)

    # Ambiguity is a clean non-zero exit (the message), not an uncaught traceback.
    with pytest.raises(SystemExit):
        _run(["resolve-kaizen-url", "--workroom-id", "wr-9"], client)


def test_resolve_kaizen_url_negative_poll_interval_rejected_by_parser(monkeypatch):
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)
    monkeypatch.setattr(cli, "wait_for_base_url", lambda *a, **k: "https://k/kaizen")

    with pytest.raises(SystemExit):
        _run(
            ["resolve-kaizen-url", "--workroom-id", "wr-9", "--poll-interval", "-1"],
            FakeClient(),
        )


def test_create_agent_uses_kaizen_base_url(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "create-agent",
            "--extension-name",
            "kaizen-legacy",
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
    # Both contracts emit the same keys; legacy has no content version.
    assert json.loads(capsys.readouterr().out) == {
        "agent_id": "agent-1",
        "version": None,
    }


def test_create_agent_missing_llm_api_key_env_exits(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)
    monkeypatch.delenv("MISSING_KEY_VAR", raising=False)

    with pytest.raises(SystemExit):
        _run(
            [
                "create-agent",
                "--extension-name", "kaizen-legacy",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
                "--model", "m",
                "--llm-api-key-env", "MISSING_KEY_VAR",
            ],
            client,
        )


def test_create_agent_defaults_to_canonical_content_contract(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "create-agent",
            "--kaizen-base-url", "https://kamiwaza.test/kaizen",
            "--name", "uat-bedrock-agent",
            "--persona", "You answer UAT smoke questions.",
            "--workroom-id", "wr-1",
        ],
        client,
    )

    # Canonical is the default identity, so no legacy call is made at all.
    assert client.agents.create.calls == []
    call = client.agents.create_canonical.calls[0]
    definition = call["args"][0]
    assert definition.name == "uat-bedrock-agent"
    assert definition.persona == "You answer UAT smoke questions."
    assert call["kwargs"]["base_url"] == "https://kamiwaza.test/kaizen"
    assert call["kwargs"]["workroom_id"] == "wr-1"
    # agent_id stays the stable output the seeder profile parses.
    assert json.loads(capsys.readouterr().out) == {"agent_id": "agent-1", "version": 1}


def test_create_agent_canonical_rejects_per_agent_model_flags(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit) as excinfo:
        _run(
            [
                "create-agent",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
                "--persona", "p",
                "--model", "openai/bedrock-uat",
                "--provider", "kamiwaza",
            ],
            client,
        )

    # The operator's model choice must never be silently dropped: the error
    # names the flags and points at the instance-level binding instead.
    message = str(excinfo.value)
    assert "--model" in message and "--provider" in message
    assert "bind-chat-model" in message
    assert client.agents.create_canonical.calls == []


def test_create_agent_canonical_rejects_custom_instructions(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    # custom_instructions only exists on the legacy body; the canonical content
    # body has no field for it, so accepting it would drop it silently.
    with pytest.raises(SystemExit, match="--custom-instructions"):
        _run(
            [
                "create-agent",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
                "--persona", "p",
                "--custom-instructions", "be terse",
            ],
            client,
        )

    assert client.agents.create_canonical.calls == []


@pytest.mark.parametrize(
    "flag,value",
    [("--persona", "p"), ("--capability-ceiling", "write")],
)
def test_create_agent_legacy_rejects_canonical_only_flags(monkeypatch, flag, value):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    # The mirror of the canonical guard: the legacy body has nowhere to put a
    # persona or a capability ceiling, so they must not be quietly accepted.
    with pytest.raises(SystemExit, match=flag):
        _run(
            [
                "create-agent",
                "--extension-name", "kaizen-legacy",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
                "--model", "m",
                flag, value,
            ],
            client,
        )

    assert client.agents.create.calls == []


def test_create_agent_validates_flags_before_scoping_the_client(monkeypatch):
    scoped = []
    monkeypatch.setattr(
        cli,
        "scoped_client_for_workroom",
        lambda c, wid: (scoped.append(wid), c)[1],
    )

    # Scoping issues a workrooms.enter session bind, so a local flag mistake
    # must never cost a server round trip.
    with pytest.raises(SystemExit):
        _run(
            [
                "create-agent",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
                "--workroom-id", "wr-1",
            ],
            FakeClient(),
        )

    assert scoped == []


def test_create_agent_canonical_requires_persona(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit, match="--persona"):
        _run(
            [
                "create-agent",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
            ],
            client,
        )


def test_create_agent_legacy_requires_model(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit, match="--model"):
        _run(
            [
                "create-agent",
                "--extension-name", "kaizen-legacy",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
            ],
            client,
        )


def test_create_agent_rejects_unknown_extension_identity():
    parser = cli.build_parser()

    # argparse choices keep an unknown identity from ever reaching the contract
    # resolver, so a typo can't silently pick a contract.
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "create-agent",
                "--kaizen-base-url", "u",
                "--name", "n",
                "--extension-name", "kaizen-next",
            ]
        )


def test_bind_chat_model_sends_only_the_deployment_id(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "bind-chat-model",
            "--kaizen-base-url", "https://kamiwaza.test/kaizen",
            "--deployment-id", "dep-xyz",
            "--workroom-id", "wr-1",
        ],
        client,
    )

    call = client.kaizen_ops.set_chat_model.calls[0]
    assert call["args"] == ("dep-xyz",)
    assert call["kwargs"]["base_url"] == "https://kamiwaza.test/kaizen"
    assert call["kwargs"]["workroom_id"] == "wr-1"
    # The write echoed the binding back, so no read-back was needed.
    assert json.loads(capsys.readouterr().out) == {
        "chat_deployment_id": "dep-xyz",
        "confirmed": True,
    }


def _bind(client, capsys=None):
    _run(
        [
            "bind-chat-model",
            "--kaizen-base-url", "https://kamiwaza.test/kaizen",
            "--deployment-id", "dep-xyz",
        ],
        client,
    )
    return json.loads(capsys.readouterr().out) if capsys else None


@pytest.mark.parametrize(
    "write_response",
    [
        {"chat": {"current": {"id": "some-other-dep"}}},
        {"chat": {"current": {"id": "dep-old"}}},
    ],
)
def test_bind_chat_model_fails_when_instance_reports_a_different_binding(
    monkeypatch, write_response
):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)
    client.kaizen_ops.set_chat_model = RecordingService(write_response)

    # The must-fail state: the instance contradicts us. Exiting 0 would let the
    # caller create and chat-verify an agent backed by an unintended model.
    with pytest.raises(SystemExit, match="contradicted"):
        _bind(client)


@pytest.mark.parametrize(
    "write_response",
    [None, {}, {"chat": {}}, {"chat": {"current": None}}, "not-a-dict"],
)
def test_bind_chat_model_reads_back_when_the_write_carries_no_binding(
    monkeypatch, capsys, write_response
):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)
    client.kaizen_ops.set_chat_model = RecordingService(write_response)
    client.kaizen_ops.get_model_settings = RecordingService(
        {"chat": {"current": {"id": "dep-xyz"}}}
    )

    # A 204 (or any body we can't read a binding out of) is an ordinary answer
    # to a settings PUT — it must not be mistaken for a wrong binding.
    out = _bind(client, capsys)

    assert len(client.kaizen_ops.get_model_settings.calls) == 1
    assert out == {"chat_deployment_id": "dep-xyz", "confirmed": True}


def test_bind_chat_model_reports_unconfirmed_when_read_back_is_also_silent(
    monkeypatch, capsys
):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)
    client.kaizen_ops.set_chat_model = RecordingService(None)
    client.kaizen_ops.get_model_settings = RecordingService({"chat": {"current": None}})

    # The write succeeded (a non-2xx would have raised); we simply can't
    # confirm it. Say so rather than implying confirmation or failing a
    # binding that probably worked.
    out = _bind(client, capsys)

    assert out == {"chat_deployment_id": "dep-xyz", "confirmed": False}


def test_bind_chat_model_survives_a_failing_read_back(monkeypatch, capsys):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)
    client.kaizen_ops.set_chat_model = RecordingService(None)
    client.kaizen_ops.get_model_settings = _raiser(KamiwazaError("ops read failed"))

    out = _bind(client, capsys)

    assert out == {"chat_deployment_id": "dep-xyz", "confirmed": False}


def test_create_agent_legacy_reads_the_secret_before_scoping_the_client(
    monkeypatch,
):
    scoped = []
    monkeypatch.setattr(
        cli,
        "scoped_client_for_workroom",
        lambda c, wid: (scoped.append(wid), c)[1],
    )
    monkeypatch.delenv("MISSING_KEY_VAR", raising=False)

    # The legacy half of the validate-before-scope ordering: a missing secret
    # must fail locally, not after the workrooms.enter round trip.
    with pytest.raises(SystemExit):
        _run(
            [
                "create-agent",
                "--extension-name", "kaizen-legacy",
                "--kaizen-base-url", "https://kamiwaza.test/kaizen",
                "--name", "a",
                "--model", "m",
                "--llm-api-key-env", "MISSING_KEY_VAR",
                "--workroom-id", "wr-1",
            ],
            FakeClient(),
        )

    assert scoped == []


def test_create_conversation(capsys):
    client = FakeClient()

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


def test_configure_connector_passes_type_and_config(capsys):
    client = FakeClient()

    _run(
        [
            "configure-connector",
            "--type",
            "m365",
            "--name",
            "Microsoft 365",
            "--config-json",
            '{"tenant_id": "tenant-abc", "client_id": "client-xyz"}',
            "--scope",
            "Files.Read.All",
        ],
        client,
    )

    request = client.connectors.create.calls[0]["args"][0]
    assert request.connector_type == "m365"
    assert request.config == {"tenant_id": "tenant-abc", "client_id": "client-xyz"}
    assert request.scopes == ["Files.Read.All"]
    assert json.loads(capsys.readouterr().out) == {
        "connector_id": "conn-1",
        "name": "Microsoft 365",
    }


def test_configure_connector_rejects_bad_config_json():
    client = FakeClient()
    with pytest.raises(SystemExit):
        _run(
            ["configure-connector", "--type", "m365", "--name", "X", "--config-json", "{bad"],
            client,
        )


def test_no_subcommand_errors():
    with pytest.raises(SystemExit):
        cli.main([], client_factory=lambda **_kw: FakeClient())


def test_parse_env_rejects_bad_pair():
    with pytest.raises(SystemExit):
        cli._parse_env(["NOTAVALIDPAIR"])


def test_deploy_model_by_id_emits_deployment_and_endpoint(capsys):
    client = FakeClient()

    _run(["deploy-model", "--model-id", "model-1"], client)

    call = client.serving.deploy_model.calls[0]["kwargs"]
    assert call["model_id"] == "model-1"
    assert call["engine_name"] == "external_chat"  # default
    assert call["wait"] is True
    # Output threads the endpoint create-agent needs for --llm-base-url.
    assert json.loads(capsys.readouterr().out) == {
        "deployment_id": "dep-xyz",
        "endpoint": "https://host/runtime/models/dep-xyz/v1",
    }


def test_deploy_model_resolves_name_to_id(capsys):
    client = FakeClient()

    _run(["deploy-model", "--name", "bedrock-uat"], client)

    # The name is resolved against the registered models, then deployed by id.
    assert client.models.list_models.calls  # lookup happened
    assert client.serving.deploy_model.calls[0]["kwargs"]["model_id"] == "model-1"


def test_deploy_model_unknown_name_exits():
    client = FakeClient()

    with pytest.raises(SystemExit):
        _run(["deploy-model", "--name", "does-not-exist"], client)

    assert client.serving.deploy_model.calls == []


def test_deploy_model_engine_name_and_no_wait_pass_through():
    client = FakeClient()

    _run(
        ["deploy-model", "--model-id", "m2", "--engine-name", "external_transcribe", "--no-wait"],
        client,
    )

    call = client.serving.deploy_model.calls[0]["kwargs"]
    assert call["engine_name"] == "external_transcribe"
    assert call["wait"] is False


def test_deploy_model_requires_model_id_or_name():
    # --model-id / --name are a required mutually-exclusive group.
    with pytest.raises(SystemExit):
        _run(["deploy-model"], FakeClient())


def test_deploy_model_nan_poll_interval_rejected_by_parser():
    with pytest.raises(SystemExit):
        _run(
            ["deploy-model", "--model-id", "m1", "--poll-interval", "nan"],
            FakeClient(),
        )


def test_deploy_model_converts_wait_failure_to_systemexit():
    # The documented wait=True failure modes must surface as a clean SystemExit,
    # not a raw traceback (matches cmd_resolve_kaizen_url).
    for exc in (DeploymentFailedError("deploy failed"), TimeoutError("deploy timed out")):
        client = FakeClient()
        client.serving.deploy_model = _raiser(exc)
        with pytest.raises(SystemExit):
            _run(["deploy-model", "--model-id", "m1"], client)


def test_deploy_model_server_refused_exits():
    # A falsy deployment id means the server refused the deploy -> clean exit.
    client = FakeClient()
    client.serving.deploy_model = RecordingService(None)
    with pytest.raises(SystemExit):
        _run(["deploy-model", "--model-id", "m1"], client)


def test_deploy_model_omits_endpoint_when_not_yet_listed(capsys):
    # --no-wait can return before DEPLOYED; list_active_deployments only returns
    # DEPLOYED ones, so the endpoint is omitted rather than wrong.
    client = FakeClient()
    client.serving.deploy_model = RecordingService("pending-dep")  # not in the listing
    _run(["deploy-model", "--model-id", "m1", "--no-wait"], client)

    out = json.loads(capsys.readouterr().out)
    assert out == {"deployment_id": "pending-dep"}
    assert "endpoint" not in out


def test_deploy_model_duplicate_name_exits():
    # Ambiguous name resolution must fail loudly, not pick one arbitrarily.
    client = FakeClient()
    client.models.list_models = RecordingService(
        [SimpleNamespace(id="m1", name="dup"), SimpleNamespace(id="m2", name="dup")]
    )
    with pytest.raises(SystemExit):
        _run(["deploy-model", "--name", "dup"], client)

    assert client.serving.deploy_model.calls == []


def test_chat_creates_conversation_and_returns_reply(capsys, monkeypatch):
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
            "--workroom-id",
            "wr-1",
        ],
        client,
    )

    create = client.conversations.create.calls[0]["kwargs"]
    assert create["agent_id"] == "agent-1"
    assert create["base_url"] == "https://kamiwaza.test/kaizen"
    chat = client.conversations.chat.calls[0]
    assert chat["args"] == ("conv-1", "hello there")
    assert chat["kwargs"]["workroom_id"] == "wr-1"
    wait = client.conversations.wait_until_ready.calls[0]
    assert wait["args"] == ("conv-1",)
    assert wait["kwargs"]["base_url"] == "https://kamiwaza.test/kaizen"
    assert wait["kwargs"]["workroom_id"] == "wr-1"
    assert wait["kwargs"]["timeout_seconds"] == 120.0
    assert json.loads(capsys.readouterr().out) == {
        "conversation_id": "conv-1",
        "reply": "Hello! I am claude.",
    }


def test_chat_sandbox_timeout_flag_controls_ready_wait(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "chat",
            "--extension-name",
            "kaizen-legacy",
            "--kaizen-base-url",
            "u",
            "--agent-id",
            "a",
            "--message",
            "m",
            "--sandbox-timeout",
            "9",
        ],
        client,
    )

    assert client.conversations.wait_until_ready.calls[0]["kwargs"]["timeout_seconds"] == 9
    assert json.loads(capsys.readouterr().out) == {
        "conversation_id": "conv-1",
        "reply": "Hello! I am claude.",
    }


def test_chat_sandbox_wait_timeout_exits_before_messaging(monkeypatch):
    client = FakeClient()
    client.conversations.wait_until_ready = _raiser(TimeoutError("sandbox not ready"))
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit, match="sandbox not ready"):
        _run(
            ["chat", "--extension-name", "kaizen-legacy", "--kaizen-base-url", "u", "--agent-id", "a", "--message", "m"],
            client,
        )

    assert client.conversations.chat.calls == []


def test_chat_raw_prints_bare_reply(capsys, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        ["chat", "--extension-name", "kaizen-legacy", "--kaizen-base-url", "u", "--agent-id", "a", "--message", "m", "--raw"],
        client,
    )

    assert capsys.readouterr().out.strip() == "Hello! I am claude."


def test_chat_empty_reply_exits_nonzero(monkeypatch):
    client = FakeClient()
    client.conversations.chat = RecordingService("")  # waited, but got nothing back
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit):
        _run(
            ["chat", "--extension-name", "kaizen-legacy", "--kaizen-base-url", "u", "--agent-id", "a", "--message", "m"],
            client,
        )


def test_chat_fire_and_forget_allows_empty_reply(capsys, monkeypatch):
    client = FakeClient()
    client.conversations.chat = RecordingService(None)
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    _run(
        [
            "chat",
            "--extension-name",
            "kaizen-legacy",
            "--kaizen-base-url", "u",
            "--agent-id", "a",
            "--message", "m",
            "--timeout", "0",
        ],
        client,
    )

    assert client.conversations.wait_until_ready.calls == []
    assert client.conversations.chat.calls[0]["kwargs"]["timeout_seconds"] == 0
    assert json.loads(capsys.readouterr().out) == {
        "conversation_id": "conv-1",
        "reply": None,
    }


def test_chat_agent_error_exits_nonzero(monkeypatch):
    from kamiwaza_sdk.services.kaizen import ConversationError

    client = FakeClient()
    client.conversations.chat = _raiser(ConversationError("agent boom"))
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit, match="agent boom"):
        _run(
            ["chat", "--extension-name", "kaizen-legacy", "--kaizen-base-url", "u", "--agent-id", "a", "--message", "m"],
            client,
        )


def test_chat_negative_timeout_rejected_by_parser():
    # argparse rejects a negative --timeout before any client call.
    with pytest.raises(SystemExit):
        _run(
            ["chat", "--kaizen-base-url", "u", "--agent-id", "a", "--message", "m",
             "--timeout", "-5"],
            FakeClient(),
        )


def test_chat_negative_poll_interval_rejected_by_parser():
    with pytest.raises(SystemExit):
        _run(
            [
                "chat",
                "--kaizen-base-url",
                "u",
                "--agent-id",
                "a",
                "--message",
                "m",
                "--poll-interval",
                "-1",
            ],
            FakeClient(),
        )


def test_chat_nan_poll_interval_rejected_by_parser():
    with pytest.raises(SystemExit):
        _run(
            [
                "chat",
                "--kaizen-base-url",
                "u",
                "--agent-id",
                "a",
                "--message",
                "m",
                "--poll-interval",
                "nan",
            ],
            FakeClient(),
        )


def test_chat_timeout_exits_nonzero(monkeypatch):
    client = FakeClient()
    client.conversations.chat = _raiser(TimeoutError("no reply in time"))
    monkeypatch.setattr(cli, "scoped_client_for_workroom", lambda c, wid: c)

    with pytest.raises(SystemExit, match="no reply in time"):
        _run(
            ["chat", "--extension-name", "kaizen-legacy", "--kaizen-base-url", "u", "--agent-id", "a", "--message", "m"],
            client,
        )


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
