from __future__ import annotations

from starlette.requests import Request

from app.main import _current_workroom_id, _safe_log_field, _workroom_role
from app.workroom_trust import auth_enabled as _auth_enabled

from .test_helpers import APP_PATH, WORKROOM_ID


def test_safe_log_field_strips_newlines() -> None:
    assert _safe_log_field("req-123\r\nforged") == "req-123forged"
    assert _safe_log_field("req\x00-123") == "req-123"


def test_safe_log_field_strips_equals_and_truncates() -> None:
    payload = "field=injected" + ("x" * 400)

    assert "=" not in _safe_log_field(payload)
    assert len(_safe_log_field(payload)) == 256


def test_auth_enabled_defaults_true(monkeypatch) -> None:
    monkeypatch.delenv("KAMIWAZA_USE_AUTH", raising=False)

    assert _auth_enabled() is True


def test_current_workroom_id_falls_back_to_forwarded_header(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) == WORKROOM_ID


def test_current_workroom_id_rejects_prefixed_app_path_without_root_path(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"{APP_PATH}/api/whoami",
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) is None


def test_current_workroom_id_does_not_trust_static_app_path_env(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) is None


def test_current_workroom_id_rejects_untrusted_forwarded_header() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "headers": [
                (b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8")),
                (b"x-forwarded-prefix", APP_PATH.encode("utf-8")),
            ],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) is None


def test_current_workroom_id_requires_authenticated_identity() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": False})()
    assert _current_workroom_id(request, identity) is None


def test_workroom_role_rejects_untrusted_forwarded_header() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "headers": [(b"x-user-workroom-role", b"admin")],
        }
    )
    identity = type(
        "Identity", (), {"workroom_role": None, "workroom_id": None, "is_authenticated": True}
    )()
    assert _workroom_role(request, identity) is None


def test_workroom_role_normalizes_forwarded_value(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [(b"x-user-workroom-role", b"Editor")],
        }
    )
    identity = type(
        "Identity", (), {"workroom_role": None, "workroom_id": None, "is_authenticated": True}
    )()
    assert _workroom_role(request, identity) == "editor"


def test_workroom_role_ignores_identity_value_outside_trusted_context() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/api/whoami", "headers": []})
    identity = type(
        "Identity",
        (),
        {"workroom_role": "editor", "workroom_id": WORKROOM_ID, "is_authenticated": True},
    )()
    assert _workroom_role(request, identity) == "editor"


def test_workroom_role_rejects_identity_value_when_forwarded_headers_poison_context() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "headers": [(b"x-user-workroom-role", b"admin")],
        }
    )
    identity = type(
        "Identity",
        (),
        {"workroom_role": "editor", "workroom_id": WORKROOM_ID, "is_authenticated": True},
    )()

    assert _workroom_role(request, identity) is None


def test_current_workroom_id_uses_identity_value_outside_routed_context() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/api/whoami", "headers": []})
    identity = type(
        "Identity",
        (),
        {"workroom_role": "editor", "workroom_id": WORKROOM_ID, "is_authenticated": True},
    )()
    assert _current_workroom_id(request, identity) == WORKROOM_ID


def test_current_workroom_id_rejects_invalid_forwarded_uuid(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [(b"x-user-workroom-id", b"not-a-uuid")],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()

    assert _current_workroom_id(request, identity) is None


def test_current_workroom_id_requires_auth_enabled_runtime(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"{APP_PATH}/api/whoami",
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) is None


def test_current_workroom_id_accepts_truthy_auth_runtime_values(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "1")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()

    assert _current_workroom_id(request, identity) == WORKROOM_ID


def test_current_workroom_id_rejects_non_runtime_root_path(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": "/apps/test-123",
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) is None


def test_current_workroom_id_rejects_cross_deployment_root_path(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": "/runtime/apps/other-deployment",
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()

    assert _current_workroom_id(request, identity) is None


def test_current_workroom_id_requires_runtime_prefix_for_forwarded_fallback(monkeypatch) -> None:
    monkeypatch.delenv("KAMIWAZA_APP_PATH", raising=False)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [(b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8"))],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()

    assert _current_workroom_id(request, identity) is None
