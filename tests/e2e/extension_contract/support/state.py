from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .common import (
    _UUID_RE,
    kubectl_secret_value,
    logger,
    resolve_deploy_login,
    safe_token_file_path,
    validated_bootstrap_arg,
    validated_username,
)
from .process_utils import parse_password_output, run_local_command


def _require_string(payload: dict[str, Any], key: str, *, path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Bootstrap state at {path} is missing {key}")
    return value


def _freeze_mapping(payload: dict[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(payload))


def _parse_personas(payload: list[Any], *, path: Path) -> Mapping[str, LivePersona]:
    personas: dict[str, LivePersona] = {}
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise TypeError(f"Bootstrap state at {path} contains non-object personas[{index}]")
        role_key = _require_string(entry, "role_key", path=path)
        personas[role_key] = LivePersona(
            role_key=role_key,
            username=_require_string(entry, "username", path=path),
            credential_ref=_require_string(entry, "credential_ref", path=path),
            expected_workroom_role=(
                str(entry["expected_workroom_role"]) if entry.get("expected_workroom_role") is not None else None
            ),
        )
    return _freeze_mapping(personas)


def _parse_workrooms(payload: dict[str, Any], *, path: Path) -> Mapping[str, str]:
    raw_workrooms = payload.get("workrooms") or {}
    if not isinstance(raw_workrooms, dict):
        raise TypeError(f"Bootstrap state at {path} has invalid workrooms object")
    parsed: dict[str, str] = {}
    for key, value in raw_workrooms.items():
        if isinstance(key, str) and value is not None:
            normalized = str(value).strip().lower()
            if not _UUID_RE.fullmatch(normalized):
                raise ValueError(f"Bootstrap state at {path} has invalid workroom id for {key}: {value!r}")
            parsed[key] = normalized
    return _freeze_mapping(parsed)


def _parse_discovered_models(payload: dict[str, Any], *, path: Path) -> tuple[Mapping[str, str], ...]:
    raw_models = payload.get("discovered_models")
    if raw_models is None:
        return ()
    if not isinstance(raw_models, list):
        raise TypeError(f"Bootstrap state at {path} has invalid discovered_models[]")
    models: list[Mapping[str, str]] = []
    for index, entry in enumerate(raw_models):
        if not isinstance(entry, dict):
            raise TypeError(f"Bootstrap state at {path} contains non-object discovered_models[{index}]")
        models.append(_freeze_mapping({str(key): str(value) for key, value in entry.items()}))
    return tuple(models)


def _parse_credential_resolution(payload: dict[str, Any], *, path: Path) -> Mapping[str, Any]:
    raw_resolution = payload.get("credential_resolution") or {}
    if not isinstance(raw_resolution, dict):
        raise TypeError(f"Bootstrap state at {path} has invalid credential_resolution object")
    helper = raw_resolution.get("helper")
    if helper is not None and not isinstance(helper, dict):
        raise TypeError(f"Bootstrap state at {path} has invalid credential_resolution.helper")
    normalized = dict(raw_resolution)
    if isinstance(helper, dict):
        normalized["helper"] = _freeze_mapping(dict(helper))
    return _freeze_mapping(normalized)


@dataclass(frozen=True)
class LivePersona:
    role_key: str
    username: str
    credential_ref: str
    expected_workroom_role: str | None


@dataclass(frozen=True)
class LiveRoutedIntegrationState:
    api_base_url: str
    app_origin: str
    verify_ssl: bool
    namespace: str
    personas: Mapping[str, LivePersona]
    workrooms: Mapping[str, str]
    discovered_models: tuple[Mapping[str, str], ...]
    credential_resolution: Mapping[str, Any]
    generated_at: str
    path: Path

    @classmethod
    def from_path(cls, path: Path) -> LiveRoutedIntegrationState:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Could not read bootstrap state at {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Bootstrap state at {path} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise TypeError(f"Bootstrap state at {path} must be a JSON object")
        personas_payload = payload.get("personas")
        if personas_payload is None:
            raise ValueError(f"Bootstrap state at {path} is missing personas[]")
        if not isinstance(personas_payload, list):
            raise TypeError(f"Bootstrap state at {path} has invalid personas[]")
        return cls(
            api_base_url=_require_string(payload, "api_base_url", path=path).rstrip("/"),
            app_origin=_require_string(payload, "app_origin", path=path).rstrip("/"),
            verify_ssl=bool(payload.get("verify_ssl", False)),
            namespace=str(payload.get("namespace") or "kamiwaza"),
            personas=_parse_personas(personas_payload, path=path),
            workrooms=_parse_workrooms(payload, path=path),
            discovered_models=_parse_discovered_models(payload, path=path),
            credential_resolution=_parse_credential_resolution(payload, path=path),
            generated_at=str(payload.get("generated_at") or ""),
            path=path,
        )

    def persona(self, role_key: str) -> LivePersona:
        persona = self.personas.get(role_key)
        if persona is None:
            raise KeyError(f"Bootstrap state does not define persona {role_key!r}")
        return persona

    def resolve_password(self, persona: LivePersona) -> str | None:
        helper = self.credential_resolution.get("helper")
        if isinstance(helper, Mapping) and helper.get("type") == "kz_login":
            configured_helper_path = str(helper.get("path") or "").strip()
            preferred = Path(configured_helper_path).expanduser() if configured_helper_path else None
            namespace = str(self.credential_resolution.get("namespace") or self.namespace)
            helper_path = resolve_deploy_login(bootstrap_path=self.path, preferred_path=preferred)
            if helper_path is not None:
                result = run_local_command(
                    [str(helper_path), "--user", validated_username(persona.username), "--namespace", validated_bootstrap_arg(namespace, field="namespace"), "--show-password"],
                    description=f"kz-login password lookup for persona {persona.role_key}",
                )
                if result is not None and result.returncode == 0 and result.stdout.strip():
                    parsed = parse_password_output(result.stdout)
                    if parsed:
                        return parsed
                elif result is not None and result.returncode != 0:
                    logger.warning(
                        "kz-login failed for persona %s with exit code %s",
                        persona.role_key,
                        result.returncode,
                    )
        if str(self.credential_resolution.get("type") or "").strip() == "secret_ref":
            namespace = str(self.credential_resolution.get("namespace") or self.namespace)
            key = str(self.credential_resolution.get("key") or "password")
            return kubectl_secret_value(persona.credential_ref, namespace, key)
        return None

    def resolve_api_key(self, persona: LivePersona) -> str | None:
        if str(self.credential_resolution.get("type") or "").strip() != "token_file":
            return None
        token_file = safe_token_file_path(persona.credential_ref, bootstrap_path=self.path)
        if not token_file.exists():
            logger.warning("Token file for persona %s does not exist: %s", persona.role_key, token_file)
            return None
        try:
            return token_file.read_text(encoding="utf-8").strip() or None
        except OSError as exc:
            logger.warning("Could not read token file for persona %s: %s", persona.role_key, exc)
            return None
