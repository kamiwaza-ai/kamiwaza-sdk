"""Provider failures retain useful classifications without exception content."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from requests import Response
from requests.exceptions import HTTPError

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.seeding.federation.keycloak import KeycloakAdmin, KeycloakAdminError
from kamiwaza_sdk.validation.cli import provider_main
from kamiwaza_sdk.validation.federation_setup import _ensure_gate
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import ProviderContractError

from .test_provider_cli import _write_prepare_inputs

pytestmark = pytest.mark.contract


def _http_failure() -> HTTPError:
    response = Response()
    response.status_code = 403
    response._content = b"secret-response-body"
    return HTTPError("secret-http-error", response=response)


@pytest.mark.parametrize(
    "case",
    [
        (_http_failure(), "http_transport", 403),
        (KeycloakAdminError("secret-identity-response"), "identity_admin", None),
        (
            APIError("secret-body", status_code=403, response_text="secret-response"),
            "sdk_api",
            403,
        ),
        (APIError("secret-body", status_code="secret-status"), "sdk_api", None),
        (APIError("secret-body", status_code=True), "sdk_api", None),
        (APIError("secret-body", status_code=9999), "sdk_api", None),
        (RuntimeError("secret-body"), "unexpected", None),
        (ProviderContractError("secret-body"), "provider_contract", None),
        (OSError("secret-body"), "io", None),
        (
            ValidationError.from_exception_data(
                "secret-class",
                [
                    {
                        "type": "value_error",
                        "loc": ("secret-location",),
                        "input": "secret-input",
                        "ctx": {"error": ValueError("secret-body")},
                    }
                ],
            ),
            "schema_validation",
            None,
        ),
        (type("secret-type-name", (Exception,), {})("secret-body"), "unexpected", None),
    ],
)
def test_cli_prepare_emits_safe_failure_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: tuple[Exception, str, int | None],
) -> None:
    error, category, status = case
    provider = GoldenProvider()
    plan, runtime, state = _write_prepare_inputs(tmp_path, provider)
    receiver = SimpleNamespace(gates=SimpleNamespace(packages=Mock()))
    receiver.gates.packages.list.side_effect = error

    def fail_prepare(*_args):
        _ensure_gate(receiver)

    monkeypatch.setattr(provider, "prepare", fail_prepare)
    rc = provider_main(
        provider,
        [
            "prepare",
            "--plan",
            str(plan),
            "--runtime",
            str(runtime),
            "--state",
            str(state),
        ],
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert "secret-" not in captured.out + captured.err
    assert str(tmp_path) not in captured.err
    payload = json.loads(
        next(line for line in captured.err.splitlines() if line.startswith("{"))
    )
    assert payload == {
        "schema": "kamiwaza.provider-diagnostic/v1",
        "phase": "prepare",
        "category": category,
        "http_status": status,
        "operation": "shared_idp.gate_fixture",
    }
    assert not state.exists()


def test_callback_metadata_does_not_publish_unrecognized_stack_locations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = GoldenProvider()
    plan, runtime, state = _write_prepare_inputs(tmp_path, provider)

    def secret_function_name(*_args):
        raise RuntimeError("secret-body")

    monkeypatch.setattr(provider, "prepare", secret_function_name)
    assert (
        provider_main(
            provider,
            [
                "prepare",
                "--plan",
                str(plan),
                "--runtime",
                str(runtime),
                "--state",
                str(state),
            ],
        )
        == 2
    )
    output = capsys.readouterr().err
    payload = json.loads(
        next(line for line in output.splitlines() if line.startswith("{"))
    )
    assert payload["operation"] == "provider_callback"
    assert "secret" not in output


def test_identity_admin_operation_is_allowlisted(monkeypatch, capsys) -> None:
    from kamiwaza_sdk.validation.cli import _provider_callback

    admin = KeycloakAdmin(
        "https://identity.example.test",
        admin_user="admin",
        admin_password="secret-password",
    )
    monkeypatch.setattr(
        admin, "_req", Mock(side_effect=KeycloakAdminError("secret-error"))
    )
    with pytest.raises(KeycloakAdminError):
        _provider_callback(lambda: admin.ensure_ropc_client("test", "test"), "prepare")
    output = capsys.readouterr().err
    assert "secret" not in output
    assert json.loads(output)["operation"] == "shared_idp.create_client"
