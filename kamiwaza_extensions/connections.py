"""Multi-connection management for kz-ext."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from kamiwaza_sdk.token_store import FileTokenStore, StoredToken

# Connection names must be safe for use as directory names
_CONN_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Hostnames where TLS verification is auto-disabled. ``.test`` is
# RFC 6761-reserved for development; ``.local`` is mDNS by RFC 6762;
# ``localhost`` is the loopback alias; bare IPs (``127.0.0.1``,
# ``192.168.x.y``) virtually never have public CA-signed certs;
# ``*.svc.cluster.local`` is in-cluster K8s service DNS. None are
# reachable from the public internet, so a self-signed cert is the
# norm. Production hostnames (``api.example.com``) keep strict
# verification.
_DEV_TLDS = (".test", ".local", ".localhost")
_K8S_INTERNAL_SUFFIX = ".svc.cluster.local"

# Mirror the SDK client's ``KAMIWAZA_VERIFY_SSL`` parsing
# (``kamiwaza_sdk.client._VERIFY_SSL_FALSE_VALUES``). Keeping the
# accepted-value sets in sync prevents divergence: e.g. with the SDK
# client treating ``0`` as off but the SDK NOT treating it as off,
# the host CLI would skip verify on outbound calls but ship strict
# settings to the deployed extension — exactly the divergence bug
# this whole module was added to prevent.
_VERIFY_SSL_FALSE_VALUES = {"false", "0", "no"}
_VERIFY_SSL_TRUE_VALUES = {"true", "1", "yes"}


def _is_dev_hostname(url: str) -> bool:
    """Return True for hostnames that conventionally use self-signed TLS."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, AttributeError):
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    if host.endswith(_K8S_INTERNAL_SUFFIX):
        return True
    if any(host.endswith(tld) for tld in _DEV_TLDS):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@dataclass
class ConnectionInfo:
    name: str
    url: str
    active: bool
    created_at: float
    verify_ssl: bool = True

    def effective_verify_ssl(self) -> bool:
        """Return the actual verify-SSL setting for this connection.

        Precedence (first match wins):

        1. ``KAMIWAZA_VERIFY_SSL=false`` env var → False (per-session
           override; the SDK client respects this for outbound calls
           and the deployed extension's TLS-reject setting must match).
        2. ``KAMIWAZA_VERIFY_SSL=true`` env var → True (explicit
           re-enable, e.g. when testing strict mode against a dev URL).
        3. URL hostname is a dev TLD (``.test``, ``.local``,
           ``localhost``, ``*.svc.cluster.local``) or a raw IP → False.
           Public CAs don't sign these; strict verify always fails.
        4. Persisted ``verify_ssl`` from ``kz-ext login`` (default True).

        Centralized so PayloadBuilder, ssl_env_override, dev_local, and
        any future caller share one source of truth — diverging
        behaviour across these paths is what produced the
        ``CERTIFICATE_VERIFY_FAILED`` symptom on cluster deploys to
        ``kamiwaza.test``.
        """
        env_value = os.environ.get("KAMIWAZA_VERIFY_SSL", "").strip().lower()
        if env_value in _VERIFY_SSL_FALSE_VALUES:
            return False
        if env_value in _VERIFY_SSL_TRUE_VALUES:
            return True
        if _is_dev_hostname(self.url):
            return False
        return self.verify_ssl


def _validate_connection_name(name: str) -> None:
    """Validate connection name is safe for filesystem paths."""
    if not _CONN_NAME_RE.match(name):
        raise ValueError(
            f"Invalid connection name '{name}'. "
            "Must contain only letters, digits, hyphens, and underscores."
        )


class ConnectionManager:
    """Manages multiple Kamiwaza connections with per-connection tokens.

    Config layout::

        ~/.kamiwaza/
        ├── token.json              # Existing SDK token (untouched)
        ├── config                  # Multi-connection metadata (JSON)
        └── connections/
            ├── default/token.json  # Per-connection PAT
            └── staging/token.json
    """

    CONFIG_VERSION = 1

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = config_dir or Path.home() / ".kamiwaza"
        self._config_path = self.config_dir / "config"
        self._connections_dir = self.config_dir / "connections"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_connection(self, name: str, url: str, token: StoredToken, *, verify_ssl: bool = True) -> None:
        """Store a new connection with its token. Sets as active if first connection."""
        _validate_connection_name(name)
        config = self._load_config()

        is_first = len(config.get("connections", {})) == 0

        config.setdefault("connections", {})[name] = {
            "url": url,
            "created_at": time.time(),
            "verify_ssl": verify_ssl,
        }

        if is_first or config.get("active_connection") is None:
            config["active_connection"] = name

        self._save_config(config)
        self._save_token(name, token)

    def remove_connection(self, name: str) -> None:
        """Remove connection and its token file."""
        _validate_connection_name(name)
        config = self._load_config()
        connections = config.get("connections", {})

        if name not in connections:
            raise ValueError(f"Connection '{name}' not found")

        del connections[name]

        if config.get("active_connection") == name:
            config["active_connection"] = next(iter(connections), None)

        self._save_config(config)

        # Remove token file
        token_path = self._token_path(name)
        if token_path.exists():
            token_path.unlink()
        token_dir = token_path.parent
        if token_dir.exists() and not any(token_dir.iterdir()):
            token_dir.rmdir()

    def list_connections(self) -> List[ConnectionInfo]:
        """Return all connections with active flag."""
        config = self._load_config()
        active = config.get("active_connection")
        result = []
        for name, info in config.get("connections", {}).items():
            result.append(ConnectionInfo(
                name=name,
                url=info["url"],
                active=(name == active),
                created_at=info.get("created_at", 0.0),
                verify_ssl=info.get("verify_ssl", True),
            ))
        return result

    def get_active_connection(self) -> Optional[ConnectionInfo]:
        """Return the currently active connection, or None."""
        config = self._load_config()
        active = config.get("active_connection")
        if active is None:
            return None
        conn = config.get("connections", {}).get(active)
        if conn is None:
            return None
        return ConnectionInfo(
            name=active,
            url=conn["url"],
            active=True,
            created_at=conn.get("created_at", 0.0),
            verify_ssl=conn.get("verify_ssl", True),
        )

    def set_active(self, name: str) -> None:
        """Switch active connection."""
        config = self._load_config()
        if name not in config.get("connections", {}):
            raise ValueError(f"Connection '{name}' not found")
        config["active_connection"] = name
        self._save_config(config)

    def get_token(self, name: Optional[str] = None) -> Optional[StoredToken]:
        """Load token for named connection (default: active)."""
        if name is None:
            config = self._load_config()
            name = config.get("active_connection")
            if name is None:
                return None
        store = FileTokenStore(self._token_path(name))
        return store.load()

    def save_token(self, token: StoredToken, name: Optional[str] = None) -> None:
        """Save token for named connection (default: active)."""
        if name is None:
            config = self._load_config()
            name = config.get("active_connection")
            if name is None:
                raise ValueError("No active connection")
        self._save_token(name, token)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _token_path(self, name: str) -> Path:
        return self._connections_dir / name / "token.json"

    def _save_token(self, name: str, token: StoredToken) -> None:
        token_path = self._token_path(name)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        store = FileTokenStore(token_path)
        store.save(token)
        # Secure file permissions
        try:
            os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)  # 600
        except OSError:
            pass

    def _load_config(self) -> dict:
        if not self._config_path.exists():
            return {"version": self.CONFIG_VERSION, "active_connection": None, "connections": {}}
        try:
            with self._config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"version": self.CONFIG_VERSION, "active_connection": None, "connections": {}}
            return data
        except (json.JSONDecodeError, OSError):
            return {"version": self.CONFIG_VERSION, "active_connection": None, "connections": {}}

    def _save_config(self, config: dict) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # Secure directory permissions
        try:
            os.chmod(self.config_dir, stat.S_IRWXU)  # 700
        except OSError:
            pass

        config["version"] = self.CONFIG_VERSION
        tmp_path = self._config_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        tmp_path.replace(self._config_path)

        # Secure file permissions
        try:
            os.chmod(self._config_path, stat.S_IRUSR | stat.S_IWUSR)  # 600
        except OSError:
            pass
