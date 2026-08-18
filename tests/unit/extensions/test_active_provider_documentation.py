"""Regression checks for provider-neutral current documentation surfaces."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RETIRED_PROVIDER = "trae" + "fik"
ACTIVE_SURFACES = (
    "docs/extensions/non-sdk-flow.md",
    "docs/services/auth/README.md",
    "docs/services/extensions/failure-classes.md",
    "examples/extensions/go-reference/README.md",
    "examples/extensions/go-reference/internal/identity/extractor.go",
    "examples/extensions/go-reference/main.go",
    "examples/kz_tech_info.md",
    "kamiwaza_extensions_lib/errors.py",
    "kamiwaza_extensions_lib/exception_names.json",
    "kamiwaza_extensions_lib/identity.py",
    "kamiwaza-openapi-spec.json",
    "tests/e2e/extension_contract/README.md",
    "tests/e2e/extension_contract/echo-check/backend/Dockerfile",
    "tests/e2e/extension_contract/echo-check/backend/app/workroom_trust.py",
    "tests/e2e/extension_contract/echo-check/docker-compose.appgarden.yml",
    "tests/e2e/extension_contract/echo-check/docker-compose.yml",
    "tests/e2e/extension_contract/support/build_ops.py",
    "tests/e2e/scenarios/runbooks/s1-user-facing-app-with-forced-login.yaml",
    "tests/e2e/scenarios/runbooks/s3-operator-deployable-connector.yaml",
)


def test_current_documentation_does_not_name_retired_provider() -> None:
    offenders = [
        path
        for path in ACTIVE_SURFACES
        if RETIRED_PROVIDER in (REPO_ROOT / path).read_text(encoding="utf-8").lower()
    ]

    assert not offenders, (
        "Current SDK documentation must describe the platform-managed ingress "
        f"contract instead of the retired provider: {offenders}"
    )
