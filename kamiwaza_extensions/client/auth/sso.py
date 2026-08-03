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
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse

import requests  # type: ignore[import-untyped]

from kamiwaza_extensions.client.config import DEFAULT_CONFIG_DIR, Config


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

    # Try cached token first
    cached = _load_cached_token(token_path)
    if (
        cached
        and isinstance(cached.get("token"), str)
        and not _is_token_expired(cached)
    ):
        return cast(str, cached["token"])

    if config.auth.non_interactive:
        raise RuntimeError(
            "Interactive Cloudflare login is disabled and no valid cached token exists"
        )

    # Cloudflare login via broker.
    token = _run_cloudflare_login(config, token_path, bucket=bucket)
    return token


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
    expiry = cached.get("expires_at")
    if expiry:
        try:
            from datetime import datetime, timezone

            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) >= exp
        except (ValueError, TypeError):
            pass
    # Fallback: decode JWT exp claim (Cloudflare Access dictates session lifetime)
    token = cached.get("token")
    if token:
        exp_ts = _jwt_exp_claim(token)
        if exp_ts is not None:
            import time

            return time.time() >= exp_ts
    return False


def _jwt_exp_claim(token: str) -> int | None:
    """Extract exp (expiration) claim from JWT payload without verification."""
    try:
        import base64

        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        # base64url: replace - with +, _ with /
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        payload = payload.replace("-", "+").replace("_", "/")
        data = json.loads(base64.b64decode(payload).decode())
        exp = data.get("exp")
        if isinstance(exp, int) and not isinstance(exp, bool):
            return exp
        return None
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def _run_cloudflare_login(
    config: Config, token_path: Path, bucket: str | None = None
) -> str:
    """Open browser to broker login; receive code via localhost callback; exchange for token."""
    broker_url = config.r2.broker_url
    if not broker_url:
        raise RuntimeError(
            "Broker URL not configured. Set R2_BROKER_URL in your environment. "
            "This is typically set by your organization for Cloudflare R2 access."
        )
    _validate_broker_url(broker_url)

    code_verifier, code_challenge = _generate_pkce_pair()
    expected_state = secrets.token_urlsafe(32)

    code_received: list[str] = []
    error_received: list[str] = []
    bucket_received: list[str] = []

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                params = parse_qs(parsed.query)
                received_state = params.get("state", [""])[0]
                if not secrets.compare_digest(received_state, expected_state):
                    error_received.append("Invalid login callback state")
                elif "code" in params:
                    code_received.append(params["code"][0])
                elif "error" in params:
                    error_received.append(params["error"][0])
                if "bucket" in params:
                    bucket_received.append(params["bucket"][0])
            self._send_response()

        def _send_response(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            display_bucket = bucket_received[0] if bucket_received else bucket
            bucket_label = (
                f" for {html.escape(display_bucket)}" if display_bucket else ""
            )
            if code_received:
                body = _load_template("callback_success.html").replace(
                    "{{bucket_label}}", bucket_label
                )
            elif error_received:
                body = (
                    _load_template("callback_error.html")
                    .replace("{{error}}", html.escape(error_received[0]))
                    .replace("{{bucket_label}}", bucket_label)
                )
            else:
                body = _load_template("callback_no_code.html").replace(
                    "{{bucket_label}}", bucket_label
                )
            self.wfile.write(body.encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Suppress server logs

    with HTTPServer(("127.0.0.1", 0), CallbackHandler) as httpd:
        port = cast(int, httpd.server_address[1])
        callback_params = {"state": expected_state}
        if bucket:
            callback_params["bucket"] = bucket
        redirect_uri = f"http://127.0.0.1:{port}/callback?{urlencode(callback_params)}"
        login_params = {
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        login_url = f"{broker_url.rstrip('/')}/login?{urlencode(login_params)}"
        sep = "=" * 72
        print(
            f"\n{sep}\n"
            f"  CLOUDFLARE LOGIN REQUIRED\n"
            f"  Open this URL in your browser to authenticate:\n"
            f"\n  {login_url}\n"
            f"{sep}\n",
            flush=True,
        )

        deadline = time.monotonic() + config.auth.callback_timeout_seconds
        while not code_received and not error_received:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            httpd.timeout = remaining
            httpd.handle_request()

    if error_received:
        raise RuntimeError(f"Cloudflare login failed: {error_received[0]}")

    if not code_received:
        raise RuntimeError(
            "Cloudflare login did not complete before the callback timeout. "
            "Please try again."
        )

    token = _exchange_code_for_token(
        code=code_received[0],
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        broker_url=broker_url,
    )
    _save_token(token_path, token, config.defaults.credential_ttl_seconds)
    return token


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
    try:
        data = resp.json()
    except requests.exceptions.JSONDecodeError as e:
        raise RuntimeError(
            f"Broker /token returned non-JSON (status {resp.status_code}). "
            "Ensure Cloudflare Access permits the token exchange endpoint."
        ) from e
    if not isinstance(data, dict):
        raise RuntimeError("Broker /token response must be a JSON object")
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Broker did not return a token")
    return cast(str, token)


def _save_token(path: Path, token: str, fallback_ttl_seconds: int = 900) -> None:
    """Save token to disk with secure permissions.

    Expiry is dictated by the Cloudflare Access JWT exp claim when decodable;
    otherwise falls back to fallback_ttl_seconds.
    """
    from datetime import datetime, timezone

    _ensure_private_directory(path.parent)
    exp_ts = _jwt_exp_claim(token)
    if exp_ts is not None:
        expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()
    else:
        from datetime import timedelta

        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=fallback_ttl_seconds)
        ).isoformat()
    data = {"token": token, "expires_at": expires_at}
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
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return
    raise RuntimeError(
        "Credential broker URL must use HTTPS (HTTP is allowed only for localhost)"
    )


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
    try:
        response_data = resp.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError("Credential broker returned a non-JSON response") from exc
    if not isinstance(response_data, dict):
        raise RuntimeError("Credential broker response must be a JSON object")
    data = cast(dict[str, Any], response_data)
    missing = [
        field
        for field in ("access_key_id", "secret_access_key")
        if not isinstance(data.get(field), str) or not data[field]
    ]
    if missing:
        raise RuntimeError(
            "Credential broker response omitted required fields: " + ", ".join(missing)
        )
    session_token = data.get("session_token")
    if session_token is not None and not isinstance(session_token, str):
        raise RuntimeError("Credential broker returned an invalid session_token")
    expiration = data.get("expiration")
    if expiration is not None and not isinstance(expiration, str):
        raise RuntimeError("Credential broker returned an invalid expiration")
    return data
