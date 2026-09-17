"""Allowlisted provider error metadata; no exception text or runtime values."""

from __future__ import annotations

import json
import sys

from pydantic import ValidationError
from requests.exceptions import RequestException

from kamiwaza_sdk.exceptions import KamiwazaError
from kamiwaza_sdk.seeding.federation.keycloak import KeycloakAdminError
from kamiwaza_sdk.validation.provider import ProviderContractError

_CATEGORIES = (
    (ValidationError, "schema_validation"),
    (ProviderContractError, "provider_contract"),
    (KamiwazaError, "sdk_api"),
    (KeycloakAdminError, "identity_admin"),
    (RequestException, "http_transport"),
    (OSError, "io"),
)
_SETUP_OPERATIONS = {
    "prepare_realm": "shared_idp.prepare_realm",
    "_prepare_external_realm": "shared_idp.external_realm",
    "_bind_edge": "shared_idp.pair_edge",
    "_configure_edge": "shared_idp.configure_edge",
    "_seed_brokered_user": "shared_idp.seed_brokered_user",
    "_ensure_gate": "shared_idp.gate_fixture",
    "_install_gate_package": "shared_idp.install_gate_package",
    "_validate_gate_discovery": "shared_idp.discover_gate",
}
_IDENTITY_OPERATIONS = {
    "_admin_token": "shared_idp.identity_admin_login",
    "create_owned_realm": "shared_idp.create_realm",
    "set_unmanaged_attributes": "shared_idp.realm_attributes",
    "ensure_ropc_client": "shared_idp.create_client",
    "ensure_attribute_mapper": "shared_idp.attribute_mapper",
    "ensure_user": "shared_idp.create_persona",
    "ropc_token": "shared_idp.persona_login",
}
_OPERATIONS = {
    "kamiwaza_sdk.validation.federation_setup": _SETUP_OPERATIONS,
    "kamiwaza_sdk.seeding.federation.keycloak": _IDENTITY_OPERATIONS,
    "kamiwaza_sdk.validation.federation_runtime": {
        "read_file_reference": "shared_idp.read_secret_reference",
        "_build_client": "shared_idp.open_cluster_client",
    },
}


def _operation(error: Exception) -> str:
    operation = "provider_callback"
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        module = frame.f_globals.get("__name__")
        registered = _OPERATIONS.get(module, {}) if isinstance(module, str) else {}
        operation = registered.get(frame.f_code.co_name, operation)
        traceback = traceback.tb_next
    return operation


def _http_status(error: Exception) -> int | None:
    status = None
    if isinstance(error, KamiwazaError):
        status = error.status_code
    elif isinstance(error, RequestException) and error.response is not None:
        status = error.response.status_code
    # Do not coerce unknown objects/strings, including an int subclass's repr.
    if type(status) is int and 100 <= status <= 599:
        return status
    return None


def emit_callback_diagnostic(error: Exception, phase: str) -> None:
    """Emit a static vocabulary only, never repr/str, locals or source lines."""
    category = next(
        (label for kind, label in _CATEGORIES if isinstance(error, kind)), "unexpected"
    )
    payload = {
        "schema": "kamiwaza.provider-diagnostic/v1",
        "phase": (
            phase
            if phase in {"describe", "resolve", "prepare", "run", "teardown"}
            else "unknown"
        ),
        "category": category,
        "http_status": _http_status(error),
        "operation": _operation(error),
    }
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)
