"""Validation for Kamiwaza platform runtime compatibility."""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from kamiwaza_extensions.validators.result import ValidationResult

_INLINE_NGINX_LISTEN_RE = re.compile(
    r"(?i)(?:^|\s)(?:echo|printf)\s+['\"][^'\"]*?\blisten\s+(?P<port>\d+)\b"
)
_NGINX_CONF_LISTEN_RE = re.compile(
    r"(?mi)^\s*listen\s+(?:(?:\[[^\]]+\]|[^\s;:]+):)?(?P<port>\d+)\b"
)
_TMP_PATH_RE = re.compile(r"(?<!\S)/tmp(?:/|\b)")
_HEREDOC_RE = re.compile(r"<<(?P<strip>-?)(?P<quote>['\"]?)(?P<marker>[A-Za-z_][A-Za-z0-9_]*)\2")
_CONFIG_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".next",
    "build",
    "dist",
    "target",
    "coverage",
}


@dataclass
class _DockerStage:
    base_ref: str
    alias: Optional[str] = None
    user: Optional[str] = None
    instructions: List[str] = field(default_factory=list)


class PlatformRuntimeValidator:
    """Validates that an extension is likely to run under Kamiwaza."""

    def validate(self, compose_path: Path, ext_dir: Path) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []

        try:
            with compose_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            return ValidationResult(passed=False, errors=[f"Invalid YAML: {exc}"])
        except FileNotFoundError:
            return ValidationResult(passed=False, errors=[f"File not found: {compose_path}"])

        if not isinstance(data, dict):
            return ValidationResult(passed=False, errors=["Compose file must be a YAML mapping"])

        services = data.get("services", {})
        if not isinstance(services, dict) or not services:
            return ValidationResult(passed=True)

        for svc_name, svc_config in services.items():
            if not isinstance(svc_config, dict):
                continue

            for port in _parse_service_ports(svc_config):
                if port < 1024:
                    errors.append(
                        f"Service '{svc_name}': container port {port} is privileged; "
                        "Kamiwaza deploys extensions as non-root, prefer 8080+"
                    )

            dockerfile_path, build_context, build_path_escaped = _resolve_build_paths(svc_config, ext_dir)
            if build_path_escaped:
                warnings.append(
                    f"Service '{svc_name}': build path escapes the extension directory and was not inspected"
                )
                continue
            if dockerfile_path is None:
                image_errors, image_warnings = _validate_image_only_service(
                    svc_name, str(svc_config.get("image") or ""),
                )
                errors.extend(image_errors)
                warnings.extend(image_warnings)
                continue
            if not dockerfile_path.exists():
                continue

            try:
                dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
            except OSError:
                warnings.append(
                    f"Service '{svc_name}': Dockerfile could not be read for runtime validation"
                )
                continue
            stages = _parse_dockerfile_stages(dockerfile_text)
            if not stages:
                continue

            final_stage = len(stages) - 1
            base_image = _resolve_effective_base_image(stages, final_stage)
            effective_user = _resolve_effective_user(stages, final_stage)
            final_text = _resolve_stage_text(stages, final_stage)
            nginx_texts = _load_nginx_config_texts(build_context, final_text) if build_context else []

            for port in _parse_exposed_ports(final_text):
                if port < 1024:
                    errors.append(
                        f"Service '{svc_name}': Dockerfile exposes privileged port {port}; "
                        "Kamiwaza deploys extensions as non-root"
                    )

            if _is_nginx_image(base_image):
                if not _has_non_root_runtime_user(base_image, effective_user):
                    errors.append(
                        f"Service '{svc_name}': nginx-based Dockerfile does not switch to a non-root user"
                    )

                combined_text = "\n".join([final_text, *nginx_texts])
                if not _TMP_PATH_RE.search(combined_text):
                    errors.append(
                        f"Service '{svc_name}': nginx runtime does not declare writable /tmp paths "
                        "required for readOnlyRootFilesystem"
                    )

                for port in _parse_nginx_listen_ports(final_text, nginx_texts):
                    if port < 1024:
                        errors.append(
                            f"Service '{svc_name}': nginx configuration listens on privileged port {port}; "
                            "prefer 8080+"
                        )

            if _is_rootful_httpd_image(base_image) and not _has_non_root_runtime_user(base_image, effective_user):
                errors.append(
                    f"Service '{svc_name}': HTTP server image does not switch to a non-root user"
                )

        return ValidationResult(
            passed=len(errors) == 0,
            errors=_dedupe(errors),
            warnings=_dedupe(warnings),
        )


def _parse_service_ports(svc_config: Dict[str, Any]) -> List[int]:
    ports = svc_config.get("ports", [])
    parsed: List[int] = []
    for port in ports:
        if isinstance(port, dict):
            target = port.get("target") or port.get("container_port")
            try:
                parsed.append(int(str(target)))
            except (TypeError, ValueError):
                continue
            continue

        port_str = str(port)
        if "/" in port_str:
            port_str = port_str.split("/", 1)[0]
        match = re.search(r"(\d+)(?:-\d+)?$", port_str)
        if match:
            parsed.append(int(match.group(1)))
    return parsed


def _resolve_build_paths(
    svc_config: Dict[str, Any], ext_dir: Path
) -> Tuple[Optional[Path], Optional[Path], bool]:
    build = svc_config.get("build")
    if isinstance(build, dict):
        context = build.get("context", ".")
        dockerfile = build.get("dockerfile", "Dockerfile")
        build_context = _resolve_relative_to_ext_dir(ext_dir, context)
        if build_context is None:
            return None, None, True
        dockerfile_path = _resolve_relative_to_ext_dir(build_context, dockerfile)
        return dockerfile_path, build_context, dockerfile_path is None
    if isinstance(build, str):
        build_context = _resolve_relative_to_ext_dir(ext_dir, build)
        if build_context is None:
            return None, None, True
        dockerfile_path = _resolve_relative_to_ext_dir(build_context, "Dockerfile")
        return dockerfile_path, build_context, dockerfile_path is None
    return None, None, False


def _resolve_relative_to_ext_dir(root: Path, relative_path: Any) -> Optional[Path]:
    candidate = (root / str(relative_path)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        return None
    return candidate


def _parse_dockerfile_stages(text: str) -> List[_DockerStage]:
    stages: List[_DockerStage] = []
    current: Optional[_DockerStage] = None

    for line in _logical_dockerfile_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.upper().startswith("FROM "):
            base_ref, alias = _parse_from_instruction(stripped)
            if not base_ref:
                continue
            current = _DockerStage(base_ref=base_ref, alias=alias)
            stages.append(current)
            continue

        if current is None:
            continue

        current.instructions.append(stripped)
        if stripped.upper().startswith("USER "):
            user = stripped[5:].strip()
            try:
                user_tokens = shlex.split(user, comments=False, posix=True)
            except ValueError:
                user_tokens = user.split()
            current.user = user_tokens[0] if user_tokens else user

    return stages


def _logical_dockerfile_lines(text: str) -> List[str]:
    logical_lines: List[str] = []
    current = ""
    lines = text.splitlines()
    idx = 0

    while idx < len(lines):
        raw_line = lines[idx]
        line = raw_line.rstrip()
        if current:
            current += " " + line.lstrip()
        else:
            current = line

        if current.endswith("\\"):
            current = current[:-1]
            idx += 1
            continue

        logical_lines.append(current)
        heredoc_markers = _extract_heredoc_markers(current)
        current = ""
        idx += 1

        if heredoc_markers:
            marker_index = 0
            while idx < len(lines) and marker_index < len(heredoc_markers):
                candidate = lines[idx].rstrip()
                marker, strip_tabs = heredoc_markers[marker_index]
                compare = candidate.lstrip("\t") if strip_tabs else candidate
                if compare == marker:
                    marker_index += 1
                idx += 1

    if current:
        logical_lines.append(current)

    return logical_lines


def _extract_heredoc_markers(line: str) -> List[Tuple[str, bool]]:
    markers: List[Tuple[str, bool]] = []
    for match in _HEREDOC_RE.finditer(line):
        markers.append((match.group("marker"), match.group("strip") == "-"))
    return markers


def parse_from_instruction(line: str) -> tuple[Optional[str], Optional[str]]:
    try:
        tokens = shlex.split(line, comments=False, posix=True)
    except ValueError:
        tokens = line.split()

    base_ref: Optional[str] = None
    alias: Optional[str] = None
    idx = 1
    while idx < len(tokens):
        token = tokens[idx]
        if token.startswith("--"):
            idx += 1
            continue
        base_ref = token
        idx += 1
        break

    while idx < len(tokens) - 1:
        if tokens[idx].upper() == "AS":
            alias = tokens[idx + 1]
            break
        idx += 1

    return base_ref, alias


def _parse_from_instruction(line: str) -> tuple[Optional[str], Optional[str]]:
    """Backward-compatible wrapper for internal callers/tests."""
    return parse_from_instruction(line)


def _resolve_effective_base_image(stages: List[_DockerStage], index: int, seen: Optional[set[str]] = None) -> str:
    alias_map = {
        stage.alias.lower(): idx for idx, stage in enumerate(stages) if stage.alias
    }
    seen = seen or set()
    base_ref = stages[index].base_ref
    key = base_ref.lower()
    alias_idx = alias_map.get(key)
    if alias_idx is None or key in seen:
        return base_ref
    seen.add(key)
    return _resolve_effective_base_image(stages, alias_idx, seen)


def _resolve_effective_user(stages: List[_DockerStage], index: int, seen: Optional[set[str]] = None) -> Optional[str]:
    alias_map = {
        stage.alias.lower(): idx for idx, stage in enumerate(stages) if stage.alias
    }
    stage = stages[index]
    if stage.user is not None:
        return stage.user

    seen = seen or set()
    key = stage.base_ref.lower()
    alias_idx = alias_map.get(key)
    if alias_idx is None or key in seen:
        return None
    seen.add(key)
    return _resolve_effective_user(stages, alias_idx, seen)


def _resolve_stage_text(stages: List[_DockerStage], index: int, seen: Optional[set[str]] = None) -> str:
    alias_map = {
        stage.alias.lower(): idx for idx, stage in enumerate(stages) if stage.alias
    }
    stage = stages[index]
    parts: List[str] = []
    seen = seen or set()
    key = stage.base_ref.lower()
    alias_idx = alias_map.get(key)
    if alias_idx is not None and key not in seen:
        seen.add(key)
        parts.append(_resolve_stage_text(stages, alias_idx, seen))
    parts.append("\n".join(stage.instructions))
    return "\n".join(part for part in parts if part)


def _parse_exposed_ports(text: str) -> List[int]:
    ports: List[int] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("EXPOSE "):
            continue
        remainder = stripped[7:].strip()
        try:
            tokens = shlex.split(remainder, comments=False, posix=True)
        except ValueError:
            tokens = remainder.split()
        for token in tokens:
            port_token = token.split("/", 1)[0]
            try:
                ports.append(int(port_token))
            except ValueError:
                continue
    return ports


def _load_nginx_config_texts(build_context: Path, final_text: str) -> List[str]:
    if not build_context.exists():
        return []

    texts: List[str] = []
    for path in sorted(_find_nginx_config_paths(build_context, final_text)):
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return texts


def _find_nginx_config_paths(build_context: Path, final_text: str) -> set[Path]:
    paths: set[Path] = set()
    for line in final_text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith(("COPY ", "ADD ")):
            continue
        paths.update(_extract_nginx_config_sources(build_context, stripped))
    return paths


def _extract_nginx_config_sources(build_context: Path, instruction: str) -> set[Path]:
    command, _, remainder = instruction.partition(" ")
    if command.upper() not in {"COPY", "ADD"} or not remainder.strip():
        return set()

    rest = remainder.strip()
    from_stage = False
    while rest.startswith("--"):
        flag, _, next_rest = rest.partition(" ")
        if not next_rest:
            return set()
        if flag.startswith("--from"):
            from_stage = True
        rest = next_rest.lstrip()

    if from_stage:
        return set()

    sources, dest = _parse_copy_arguments(rest)
    if not sources or not _is_nginx_config_destination(dest):
        return set()

    resolved: set[Path] = set()
    for source in sources:
        resolved.update(_resolve_copy_source_paths(build_context, source))
    return resolved


def _parse_copy_arguments(text: str) -> Tuple[List[str], str]:
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            items = json.loads(stripped)
        except json.JSONDecodeError:
            return [], ""
        if not isinstance(items, list) or len(items) < 2:
            return [], ""
        values = [str(item) for item in items]
        return values[:-1], values[-1]

    try:
        tokens = shlex.split(stripped, comments=False, posix=True)
    except ValueError:
        tokens = stripped.split()
    if len(tokens) < 2:
        return [], ""
    return tokens[:-1], tokens[-1]


def _is_nginx_config_destination(dest: str) -> bool:
    lower = dest.lower()
    return (
        lower.startswith("/etc/nginx/")
        or "/nginx/conf.d/" in lower
        or ("/nginx/conf/" in lower and lower.endswith(".conf"))
        or lower.endswith("/nginx.conf")
    )


def _resolve_copy_source_paths(build_context: Path, source: str) -> set[Path]:
    if not source or source == ".":
        return set()

    candidates: set[Path] = set()
    if any(token in source for token in "*?["):
        for match in build_context.glob(source):
            candidates.update(_collect_config_paths(match))
        return candidates

    candidate = _resolve_relative_to_ext_dir(build_context, source)
    if candidate is None:
        return set()
    return _collect_config_paths(candidate)


def _collect_config_paths(path: Path) -> set[Path]:
    if path.is_file():
        return {path}
    if not path.is_dir():
        return set()

    paths: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in _CONFIG_SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".conf") or filename == "nginx.conf":
                paths.add(Path(dirpath) / filename)
    return paths


def _parse_nginx_listen_ports(final_text: str, config_texts: Iterable[str]) -> List[int]:
    ports: List[int] = []
    for match in _INLINE_NGINX_LISTEN_RE.finditer(final_text):
        ports.append(int(match.group("port")))
    for text in config_texts:
        for match in _NGINX_CONF_LISTEN_RE.finditer(text):
            ports.append(int(match.group("port")))
    return ports


def _is_non_root_user(user: Optional[str]) -> bool:
    if not user:
        return False
    normalized = user.strip().lower()
    if normalized == "root":
        return False

    primary = normalized.split(":", 1)[0]
    if primary.isdigit():
        return int(primary) != 0
    if primary == "root":
        return False
    return True


def _is_nginx_image(base_image: str) -> bool:
    base = _image_basename(base_image)
    return base == "nginx" or base.startswith("nginx-")


def _has_non_root_runtime_user(base_image: str, user: Optional[str]) -> bool:
    if user is not None:
        return _is_non_root_user(user)
    return _default_image_user_is_non_root(base_image)


def _default_image_user_is_non_root(base_image: str) -> bool:
    lower = base_image.lower()
    return "unprivileged" in lower or "nonroot" in lower or "non-root" in lower


def _is_rootful_httpd_image(base_image: str) -> bool:
    base = _image_basename(base_image)
    return (
        base == "httpd"
        or base.startswith("httpd-")
        or base == "apache"
        or base.startswith("apache-")
    )


def _validate_image_only_service(
    svc_name: str,
    image_ref: str,
) -> Tuple[List[str], List[str]]:
    if not image_ref:
        return [], []

    errors: List[str] = []
    warnings: List[str] = []

    if _is_nginx_image(image_ref):
        if not _has_non_root_runtime_user(image_ref, user=None):
            errors.append(
                f"Service '{svc_name}': image-only nginx service '{image_ref}' is likely rootful; "
                "use an unprivileged image or provide a Dockerfile that switches users"
            )
        warnings.append(
            f"Service '{svc_name}': image-only nginx service '{image_ref}' could not be inspected "
            "for listen ports or writable /tmp runtime paths"
        )
    elif _is_rootful_httpd_image(image_ref) and not _has_non_root_runtime_user(image_ref, user=None):
        errors.append(
            f"Service '{svc_name}': image-only HTTP server image '{image_ref}' is likely rootful; "
            "use a non-root image or provide a Dockerfile that switches users"
        )

    return errors, warnings


def _image_basename(image_ref: str) -> str:
    ref = image_ref.split("@", 1)[0]
    name = ref.rsplit("/", 1)[-1]
    return name.split(":", 1)[0].lower()


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped
