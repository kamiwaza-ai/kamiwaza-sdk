from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EXTENSION_FIXTURES_ROOT = Path(__file__).resolve().parent


def _extension_version(extension_name: str) -> str:
    metadata_path = EXTENSION_FIXTURES_ROOT / extension_name / "kamiwaza.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    version = str(payload.get("version") or "").strip()
    if not version:
        raise ValueError(f"Missing version in {metadata_path}")
    return version


@dataclass(frozen=True)
class AppSmokeContract:
    extension_name: str
    template_name: str
    template_version: str | None = None
    build_before_deploy: bool = False
    root_probe_path: str = ""
    readiness_path: str = "/api/ready"
    smoke_path: str = "/api/agents/"
    smoke_json_key: str = "agents"
    requires_auth: bool = True
    secret_encryption_key_env_var: str | None = None

    def resolved_template_version(self) -> str:
        if self.template_version:
            return self.template_version
        return _extension_version(self.extension_name)


ECHO_CHECK = AppSmokeContract(
    extension_name="echo-check",
    template_name="Echo Check",
    build_before_deploy=True,
    smoke_path="/api/runtime",
    smoke_json_key="kamiwaza_app_path",
    requires_auth=True,
)
