from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

import requests

from .process_utils import decode_secret_value, parse_password_output, run_local_command

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTENSION_FIXTURES_ROOT = REPO_ROOT / "tests" / "extension_contract"
DEFAULT_BOOTSTRAP_STATE = Path(".artifacts/live-routed-integration/bootstrap-state.json")
DEFAULT_DEPLOYMENT_ARTIFACT_DIR = Path(".artifacts/live-extensions")
_KUBECTL_SECRET_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_KUBECTL_RESOURCE_NAME_RE = re.compile(r"^[a-z0-9.-]+$")
_KNOWN_KZ_LOGIN_NAMES = frozenset({"kz-login"})
_SAFE_BOOTSTRAP_ARG_RE = re.compile(r"^[a-z0-9-]+$")
_SAFE_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]+$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
logger = logging.getLogger(__name__)


def env_flag(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def origin_from_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    return normalized[:-4] if normalized.endswith("/api") else normalized


def deployment_env_overrides() -> dict[str, str]:
    overrides: dict[str, str] = {}
    prefix = "LIVE_EXTENSION_DEPLOY_ENV_"
    for key, value in os.environ.items():
        if key.startswith(prefix):
            env_name = key[len(prefix) :].strip()
            if env_name and value.strip():
                overrides[env_name] = value.strip()
    return overrides


def bootstrap_state_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv("LIVE_ROUTED_INTEGRATION_STATE", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.exists():
            raise FileNotFoundError(
                f"LIVE_ROUTED_INTEGRATION_STATE points to a missing file: {configured_path}"
            )
        candidates.append(configured_path)
    candidates.append(REPO_ROOT.parent / "deploy" / DEFAULT_BOOTSTRAP_STATE)
    candidates.append(REPO_ROOT / DEFAULT_BOOTSTRAP_STATE)
    return candidates


def validated_bootstrap_arg(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if not _SAFE_BOOTSTRAP_ARG_RE.fullmatch(normalized):
        raise ValueError(f"Bootstrap state contains invalid {field}: {value!r}")
    return normalized


def validated_username(value: str) -> str:
    normalized = value.strip()
    if not _SAFE_USERNAME_RE.fullmatch(normalized):
        raise ValueError(f"Bootstrap state contains invalid username: {value!r}")
    return normalized


def validated_kubectl_resource_name(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if not _KUBECTL_RESOURCE_NAME_RE.fullmatch(normalized):
        raise ValueError(f"Bootstrap state contains invalid {field}: {value!r}")
    return normalized


def _trusted_login_roots() -> tuple[Path, Path]:
    return (
        (REPO_ROOT.parent / "deploy" / "scripts").resolve(),
        (Path.home() / ".kamiwaza" / "scripts").resolve(),
    )


def _configured_login_path() -> Path | None:
    configured_path = os.getenv("LIVE_EXTENSION_KZ_LOGIN_PATH")
    return Path(configured_path).expanduser() if configured_path else None


def _trusted_login_candidates(
    configured_path: Path | None,
    trusted_roots: tuple[Path, Path],
) -> list[Path]:
    candidates = [
        candidate
        for candidate in (
            configured_path,
            trusted_roots[0] / "kz-login",
            trusted_roots[1] / "kz-login",
        )
        if candidate is not None
    ]
    trusted: list[Path] = []
    for candidate in candidates:
        if candidate.name not in _KNOWN_KZ_LOGIN_NAMES or not candidate.exists():
            continue
        resolved = candidate.resolve()
        if any(resolved.is_relative_to(root) for root in trusted_roots):
            if resolved not in trusted:
                trusted.append(resolved)
            continue
        if configured_path is not None and resolved == configured_path.resolve():
            logger.warning(
                "Ignoring LIVE_EXTENSION_KZ_LOGIN_PATH outside trusted roots: %s",
                candidate,
            )
    return trusted


def resolve_deploy_login(
    *,
    bootstrap_path: Path | None = None,
    preferred_path: Path | None = None,
) -> Path | None:
    trusted_roots = _trusted_login_roots()
    trusted = _trusted_login_candidates(_configured_login_path(), trusted_roots)
    if preferred_path is not None:
        preferred = preferred_path.expanduser()
        if preferred.name not in _KNOWN_KZ_LOGIN_NAMES:
            logger.warning("Ignoring untrusted kz-login helper path from bootstrap state: %s", preferred_path)
        elif preferred.exists():
            resolved = preferred.resolve()
            if resolved in trusted:
                return resolved
            logger.warning("Ignoring bootstrap kz-login helper outside trusted roots: %s", preferred_path)
    return trusted[0] if trusted else None


def load_local_admin_password() -> str | None:
    deploy_login = resolve_deploy_login()
    if deploy_login is None:
        logger.warning("Could not resolve deploy kz-login helper for local admin password")
        return None
    result = run_local_command([str(deploy_login), "--show-password"], description="kz-login --show-password")
    if result is None:
        return None
    if result.returncode != 0:
        logger.warning("kz-login --show-password failed with exit code %s: %s", result.returncode, result.stderr.strip() or result.stdout.strip())
        return None
    parsed = parse_password_output(result.stdout)
    if parsed is None:
        logger.warning("kz-login --show-password returned no parseable password output")
    return parsed


def kubectl_secret_value(secret_name: str, namespace: str, key: str) -> str | None:
    kubectl_path = shutil.which("kubectl")
    if kubectl_path is None:
        return None
    secret_name = validated_kubectl_resource_name(secret_name, field="secret_name")
    namespace = validated_kubectl_resource_name(namespace, field="namespace")
    if not _KUBECTL_SECRET_KEY_RE.fullmatch(key):
        raise ValueError(f"Invalid kubectl secret key {key!r}")
    result = run_local_command(
        [kubectl_path, "-n", namespace, "get", "secret", secret_name, "-o", f"jsonpath={{.data['{key}']}}"],
        description=f"kubectl secret lookup for {namespace}/{secret_name} key {key}",
    )
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    return decode_secret_value(result.stdout.strip())


def safe_token_file_path(raw_path: str, *, bootstrap_path: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    resolved = (bootstrap_path.parent / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    allowed_roots = (bootstrap_path.parent.resolve(), REPO_ROOT.resolve(), (Path.home() / ".kamiwaza").resolve())
    if any(resolved.is_relative_to(root) for root in allowed_roots):
        return resolved
    raise ValueError(f"Token file path {raw_path!r} is outside allowed live-test roots")


def ping_response_ok(response: requests.Response) -> bool:
    return response.status_code == 200 or (
        response.status_code == 401 and "not authenticated" in response.text.lower()
    )
