"""Cloudflare auth + credential broker exchange.

Uses Cloudflare for authentication (Google SSO is integrated in Cloudflare).
Prints login URL; user opens it in browser and logs in via Cloudflare.
Uses authorization code flow with PKCE (OAuth 2.0 best practice).
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse

import requests  # type: ignore[import-untyped]

from kamiwaza_extensions.client.config import DEFAULT_CONFIG_DIR, Config

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_REQUIRED_CREDENTIAL_FIELDS = ("access_key_id", "secret_access_key")
_OPTIONAL_CREDENTIAL_FIELDS = ("session_token", "expiration")


def _load_template(name: str) -> str:
    """Load HTML template from package templates directory."""
    path = resources.files("kamiwaza_extensions.client.auth") / "templates" / name
    return path.read_text(encoding="utf-8")


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256).

    Returns:
        (code_verifier, code_challenge)
    """
    code_verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    )
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return code_verifier, code_challenge


def get_cloudflare_token(config: Config, bucket: str | None = None) -> str:
    """Obtain Cloudflare auth token (cached or via browser login).

    When no valid cached token exists, prints the broker login URL. The user
    opens it and logs in via Cloudflare; the broker redirects back with a code.

    Args:
        config: R2 client config.
        bucket: Optional bucket name; when provided, shown on success/error pages.

    Raises:
        RuntimeError: If no valid token can be obtained.
    """
    token_path = Path(config.auth.token_cache_path).expanduser()
    _ensure_private_directory(token_path.parent)

    cached_token = _valid_cached_token(token_path)
    if cached_token is not None:
        return cached_token

    if config.auth.non_interactive:
        raise RuntimeError(
            "Interactive Cloudflare login is disabled and no valid cached token exists"
        )

    return _run_cloudflare_login(config, token_path, bucket=bucket)


def _valid_cached_token(token_path: Path) -> str | None:
    """Return the cached token when one is present and still valid."""
    cached = _load_cached_token(token_path)
    if not cached:
        return None
    token = cached.get("token")
    if not isinstance(token, str):
        return None
    return None if _is_token_expired(cached) else token


def _load_cached_token(path: Path) -> dict[str, Any] | None:
    """Load cached token from disk."""
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Token cache must be a regular file: {path}")
    try:
        path.chmod(0o600)
        with path.open(encoding="utf-8") as f:
            cached = json.load(f)
        if not isinstance(cached, dict):
            return None
        return cast(dict[str, Any], cached)
    except (json.JSONDecodeError, OSError):
        return None


def _is_token_expired(cached: dict[str, Any]) -> bool:
    """Check if cached token is expired (best-effort).

    Uses expires_at from cache, or decodes JWT exp claim if present.
    Expiry is dictated by the Cloudflare Access application's session config.
    """
    declared_expiry = _parse_iso_expiry(cached.get("expires_at"))
    if declared_expiry is not None:
        return datetime.now(timezone.utc) >= declared_expiry

    # Fallback: decode JWT exp claim (Cloudflare Access dictates session lifetime)
    token = cached.get("token")
    exp_ts = _jwt_exp_claim(token) if token else None
    return exp_ts is not None and time.time() >= exp_ts


def _parse_iso_expiry(expiry: Any) -> datetime | None:
    """Parse a cached ISO-8601 expiry, tolerating a trailing ``Z``."""
    if not expiry:
        return None
    try:
        return datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _jwt_exp_claim(token: str) -> int | None:
    """Extract exp (expiration) claim from JWT payload without verification."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        data = json.loads(_decode_jwt_segment(parts[1]))
        exp = data.get("exp")
        if isinstance(exp, int) and not isinstance(exp, bool):
            return exp
        return None
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def _decode_jwt_segment(payload: str) -> str:
    """Decode one base64url JWT segment to text."""
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    payload = payload.replace("-", "+").replace("_", "/")
    return base64.b64decode(payload).decode()


@dataclass
class _CallbackResult:
    """What the local callback server captured from the broker redirect."""

    codes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)

    @property
    def settled(self) -> bool:
        return bool(self.codes or self.errors)


def _callback_handler_class(
    result: _CallbackResult,
    expected_state: str,
    bucket: str | None,
) -> type[BaseHTTPRequestHandler]:
    """Build the one-shot callback handler bound to this login attempt."""

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                _record_callback_params(
                    result, parse_qs(parsed.query), expected_state
                )
            self._send_response()

        def _send_response(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_callback_body(result, bucket).encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Suppress server logs

    return CallbackHandler


def _record_callback_params(
    result: _CallbackResult,
    params: dict[str, list[str]],
    expected_state: str,
) -> None:
    """Record the redirect outcome, rejecting a mismatched state."""
    received_state = params.get("state", [""])[0]
    if not secrets.compare_digest(received_state, expected_state):
        result.errors.append("Invalid login callback state")
    elif "code" in params:
        result.codes.append(params["code"][0])
    elif "error" in params:
        result.errors.append(params["error"][0])
    if "bucket" in params:
        result.buckets.append(params["bucket"][0])


def _callback_body(result: _CallbackResult, bucket: str | None) -> str:
    """Render the browser-facing page for the completed redirect."""
    display_bucket = result.buckets[0] if result.buckets else bucket
    bucket_label = f" for {html.escape(display_bucket)}" if display_bucket else ""
    if result.codes:
        return _load_template("callback_success.html").replace(
            "{{bucket_label}}", bucket_label
        )
    if result.errors:
        return (
            _load_template("callback_error.html")
            .replace("{{error}}", html.escape(result.errors[0]))
            .replace("{{bucket_label}}", bucket_label)
        )
    return _load_template("callback_no_code.html").replace(
        "{{bucket_label}}", bucket_label
    )


def _run_cloudflare_login(
    config: Config, token_path: Path, bucket: str | None = None
) -> str:
    """Open browser to broker login; receive code via localhost callback; exchange for token."""
    broker_url = _require_broker_url(config)
    code_verifier, code_challenge = _generate_pkce_pair()
    expected_state = secrets.token_urlsafe(32)
    result = _CallbackResult()

    handler = _callback_handler_class(result, expected_state, bucket)
    with HTTPServer(("127.0.0.1", 0), handler) as httpd:
        port = cast(int, httpd.server_address[1])
        redirect_uri = _build_redirect_uri(port, expected_state, bucket)
        _print_login_prompt(
            _build_login_url(broker_url, redirect_uri, code_challenge)
        )
        _await_callback(httpd, result, config.auth.callback_timeout_seconds)

    code = _require_callback_code(result)
    token = _exchange_code_for_token(
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        broker_url=broker_url,
    )
    _save_token(token_path, token, config.defaults.credential_ttl_seconds)
    return token


def _require_broker_url(config: Config) -> str:
    """Return the configured broker URL, or explain how to set it."""
    broker_url = config.r2.broker_url
    if not broker_url:
        raise RuntimeError(
            "Broker URL not configured. Set R2_BROKER_URL in your environment. "
            "This is typically set by your organization for Cloudflare R2 access."
        )
    _validate_broker_url(broker_url)
    return broker_url


def _build_redirect_uri(port: int, expected_state: str, bucket: str | None) -> str:
    callback_params = {"state": expected_state}
    if bucket:
        callback_params["bucket"] = bucket
    return f"http://127.0.0.1:{port}/callback?{urlencode(callback_params)}"


def _build_login_url(broker_url: str, redirect_uri: str, code_challenge: str) -> str:
    login_params = {
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{broker_url.rstrip('/')}/login?{urlencode(login_params)}"


def _print_login_prompt(login_url: str) -> None:
    sep = "=" * 72
    print(
        f"\n{sep}\n"
        f"  CLOUDFLARE LOGIN REQUIRED\n"
        f"  Open this URL in your browser to authenticate:\n"
        f"\n  {login_url}\n"
        f"{sep}\n",
        flush=True,
    )


def _await_callback(
    httpd: HTTPServer, result: _CallbackResult, timeout_seconds: float
) -> None:
    """Serve requests until the redirect arrives or the deadline passes."""
    deadline = time.monotonic() + timeout_seconds
    while not result.settled:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        httpd.timeout = remaining
        httpd.handle_request()


def _require_callback_code(result: _CallbackResult) -> str:
    """Return the authorization code, or raise the reason there is none."""
    if result.errors:
        raise RuntimeError(f"Cloudflare login failed: {result.errors[0]}")
    if not result.codes:
        raise RuntimeError(
            "Cloudflare login did not complete before the callback timeout. "
            "Please try again."
        )
    return result.codes[0]


def _exchange_code_for_token(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    broker_url: str,
) -> str:
    """Exchange authorization code for JWT (PKCE flow)."""
    _validate_broker_url(broker_url)
    resp = requests.post(
        broker_url.rstrip("/") + "/token",
        json={
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = _broker_json_object(
        resp,
        non_json_message=(
            f"Broker /token returned non-JSON (status {resp.status_code}). "
            "Ensure Cloudflare Access permits the token exchange endpoint."
        ),
        non_object_message="Broker /token response must be a JSON object",
    )
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Broker did not return a token")
    return token


def _broker_json_object(
    resp: Any, *, non_json_message: str, non_object_message: str
) -> dict[str, Any]:
    """Decode a broker response body that must be a JSON object."""
    try:
        payload = resp.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(non_json_message) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(non_object_message)
    return cast(dict[str, Any], payload)


def _save_token(path: Path, token: str, fallback_ttl_seconds: int = 900) -> None:
    """Save token to disk with secure permissions.

    Expiry is dictated by the Cloudflare Access JWT exp claim when decodable;
    otherwise falls back to fallback_ttl_seconds.
    """
    _ensure_private_directory(path.parent)
    data = {"token": token, "expires_at": _token_expiry(token, fallback_ttl_seconds)}
    _write_private_json(path, data)


def _token_expiry(token: str, fallback_ttl_seconds: int) -> str:
    """Prefer the JWT's own exp claim; fall back to a fixed TTL."""
    exp_ts = _jwt_exp_claim(token)
    if exp_ts is not None:
        return datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()
    return (
        datetime.now(timezone.utc) + timedelta(seconds=fallback_ttl_seconds)
    ).isoformat()


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write owner-only JSON to path."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as token_file:
            json.dump(data, token_file, indent=2)
        temp_path.replace(path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def _ensure_private_directory(path: Path) -> None:
    """Create or tighten a directory used to store authentication state."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise RuntimeError(f"Token directory is not owned by the current user: {path}")
    permissions = stat.S_IMODE(path.stat().st_mode)
    if permissions & 0o077 and path != DEFAULT_CONFIG_DIR.expanduser():
        raise RuntimeError(f"Token directory permissions are too broad: {path}")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise RuntimeError(f"Could not secure token directory: {path}") from exc


def _validate_broker_url(broker_url: str) -> None:
    """Require a trustworthy HTTP origin for credential-broker requests."""
    parsed = urlparse(broker_url)
    if parsed.username or parsed.password:
        raise RuntimeError("Credential broker URL must not contain user information")
    if not _is_trusted_origin(parsed.scheme, parsed.hostname):
        raise RuntimeError(
            "Credential broker URL must use HTTPS (HTTP is allowed only for localhost)"
        )


def _is_trusted_origin(scheme: str, hostname: str | None) -> bool:
    if not hostname:
        return False
    if scheme == "https":
        return True
    return scheme == "http" and hostname in _LOCAL_HOSTS


def exchange_token_for_credentials(
    token: str,
    broker_url: str,
    *,
    bucket: str | None = None,
) -> dict[str, Any]:
    """Exchange Cloudflare auth token for temporary R2 credentials.

    When bucket is provided, requests credentials for that bucket. Useful when
    the user is in multiple groups (e.g. developer + ops) and the default
    role-based bucket would be prod; pass bucket='dev-kevin-test' to get
    dev credentials instead.

    Returns:
        Dict with access_key_id, secret_access_key, session_token, expiration.
    """
    _validate_broker_url(broker_url)
    payload: dict[str, Any] = {"token": token}
    if bucket:
        payload["bucket"] = bucket
    resp = requests.post(
        broker_url.rstrip("/") + "/credentials",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = _broker_json_object(
        resp,
        non_json_message="Credential broker returned a non-JSON response",
        non_object_message="Credential broker response must be a JSON object",
    )
    _validate_credentials_payload(data)
    return data


def _is_non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _validate_credentials_payload(data: dict[str, Any]) -> None:
    """Reject a credential payload missing or mistyping any documented field."""
    _require_credential_fields(data)
    _reject_mistyped_optional_fields(data)


def _require_credential_fields(data: dict[str, Any]) -> None:
    """Every credential set must carry a usable key pair."""
    missing = [
        name
        for name in _REQUIRED_CREDENTIAL_FIELDS
        if not _is_non_empty_str(data.get(name))
    ]
    if missing:
        raise RuntimeError(
            "Credential broker response omitted required fields: " + ", ".join(missing)
        )


def _reject_mistyped_optional_fields(data: dict[str, Any]) -> None:
    """Optional fields may be absent, but never the wrong type."""
    for name in _OPTIONAL_CREDENTIAL_FIELDS:
        value = data.get(name)
        if value is not None and not isinstance(value, str):
            raise RuntimeError(f"Credential broker returned an invalid {name}")
