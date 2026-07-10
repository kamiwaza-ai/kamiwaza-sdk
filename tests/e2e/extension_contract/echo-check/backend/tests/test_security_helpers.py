from __future__ import annotations

from starlette.requests import Request

from app.main import _current_workroom_id, _safe_log_field, _workroom_role
from app.workroom_trust import auth_enabled as _auth_enabled

from .test_helpers import APP_PATH, TRUSTED_PROXY_SECRET, WORKROOM_ID

# ENG-5956 follow-up: ``trusted_routed_workroom_context`` now requires both
# root_path AND the proxy-injected shared-secret header. Positive-trust
# tests below set the env var and inject the header; negative tests omit
# one or both.
TRUSTED_PROXY_HEADER = (b"x-kamiwaza-trusted-proxy", TRUSTED_PROXY_SECRET.encode("utf-8"))


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
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [
                (b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8")),
                TRUSTED_PROXY_HEADER,
            ],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) == WORKROOM_ID


def test_current_workroom_id_rejects_forged_root_path_without_trusted_proxy_header(monkeypatch) -> None:
    """ENG-5956 follow-up (kamiwaza-sdk#134 self-review H1) regression test.

    A direct caller hitting the container port with `--root-path` set
    (uvicorn populates `scope['root_path']` unconditionally) and forged
    `x-user-*` headers MUST NOT be trusted. The new shared-secret marker
    closes the gap that dropping the app-level prefix opened.
    """
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,  # forged via direct uvicorn --root-path
            "headers": [
                (b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8")),
                (b"x-user-workroom-role", b"admin"),
                # NOTE: no x-kamiwaza-trusted-proxy header — direct traffic
                # does not have it.
            ],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) is None
    assert _workroom_role(request, identity) is None


def test_current_workroom_id_rejects_wrong_trusted_proxy_secret(monkeypatch) -> None:
    """An attacker who guesses the header NAME but not the SECRET is rejected."""
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [
                (b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8")),
                (b"x-kamiwaza-trusted-proxy", b"wrong-secret"),
            ],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) is None


def test_has_trusted_proxy_marker_fails_closed_on_non_ascii_header(monkeypatch) -> None:
    """ENG-6495: Starlette decodes inbound header values as latin-1, so any
    byte >= 0x80 yields a non-ASCII `str`. The original implementation called
    ``hmac.compare_digest(str, str)``, which raises ``TypeError`` on such
    inputs ("comparing strings with non-ASCII characters is not supported")
    and surfaces as an unhandled 500 — distinguishing a gated protected
    route from a truly absent route. The fix encodes both sides as UTF-8
    bytes and wraps in a defensive try/except so any other exotic input
    also returns ``False`` (fail-closed) rather than raising.
    """
    from app.workroom_trust import has_trusted_proxy_marker

    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)

    # latin-1 byte 0xff decodes to "ÿ" (U+00FF) — a single non-ASCII char.
    # We pass it through the headers exactly as Starlette would deliver it.
    raw_marker = bytes([0xFF])
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [(b"x-kamiwaza-trusted-proxy", raw_marker)],
        }
    )

    # Must return False, NOT raise. The old implementation would raise
    # TypeError inside compare_digest, which FastAPI would translate to
    # an unhandled 500.
    assert has_trusted_proxy_marker(request) is False


def test_has_trusted_proxy_marker_matches_correct_secret(monkeypatch) -> None:
    """Positive control: the bytes-encoded compare still accepts the
    matching ASCII secret. Guards against an over-aggressive try/except
    that silently rejects all inputs.
    """
    from app.workroom_trust import has_trusted_proxy_marker

    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [TRUSTED_PROXY_HEADER],
        }
    )

    assert has_trusted_proxy_marker(request) is True


def test_current_workroom_id_fails_closed_when_trusted_proxy_secret_unset(monkeypatch) -> None:
    """If the deployment hasn't configured KAMIWAZA_TRUSTED_PROXY_SECRET,
    the trusted-routed path is unavailable even with matching root_path
    and any header value. Extensions MUST opt in explicitly."""
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.delenv("KAMIWAZA_TRUSTED_PROXY_SECRET", raising=False)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [
                (b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8")),
                TRUSTED_PROXY_HEADER,
            ],
        }
    )
    identity = type("Identity", (), {"workroom_id": None, "is_authenticated": True})()
    assert _current_workroom_id(request, identity) is None


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
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [
                (b"x-user-workroom-role", b"Editor"),
                TRUSTED_PROXY_HEADER,
            ],
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
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/whoami",
            "root_path": APP_PATH,
            "headers": [
                (b"x-user-workroom-id", WORKROOM_ID.upper().encode("utf-8")),
                TRUSTED_PROXY_HEADER,
            ],
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
