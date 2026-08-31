"""Static contract and resolution for delegated-workload validation."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kamiwaza_sdk.schemas.delegated_jobs import normalize_python_packages
from kamiwaza_sdk.validation.applicability import ApplicableTarget
from kamiwaza_sdk.validation.federation_spec import (
    install_requirements,
    resolve_candidates as resolve_shared_idp_candidates,
)
from kamiwaza_sdk.validation.models import (
    FactMatcher,
    ResolvedScenario,
    ScenarioDescriptor,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import ProviderContractError

DELEGATED_PROVIDER_ID = "sdk.federation.delegated-workload"
DELEGATED_PROVIDER_REVISION = "sdk.federation.delegated-workload@v1"
DELEGATED_SCENARIO_ID = "sdk.federation.delegated-workload/v1"
DELEGATED_CASE_IDS = ("delegated-job-approved-package",)
DELEGATED_FEATURE_ID = "federation/delegated-workload:v1"

_PACKAGES_ENV = "KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON"
_IMPORTS_ENV = "KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON"
_IMPORT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


@dataclass(frozen=True)
class DelegatedPackageConfig:
    """Non-secret package fixture inputs carried by a resolved selection."""

    coordinates: tuple[str, ...]
    import_names: tuple[str, ...]
    expected_versions: dict[str, str]


def scenario_descriptor() -> ScenarioDescriptor:
    """Describe the comprehensive, capability-gated delegated edge."""

    return ScenarioDescriptor(
        scenario_id=DELEGATED_SCENARIO_ID,
        provider_id=DELEGATED_PROVIDER_ID,
        protocol_version="v1",
        target_scope="mesh_edge",
        minimum_level="comprehensive",
        capability_ids=(
            "federation.remote-jobs",
            "federation.delegated-workload",
        ),
        applies_when=(
            FactMatcher(
                path=("edge", "identity_mode"),
                operator="eq",
                value="shared_idp",
            ),
            FactMatcher(
                path=("edge", "capabilities"),
                operator="contains",
                value=DELEGATED_FEATURE_ID,
            ),
        ),
        requires=("cluster-api", "ownership-key"),
        fixture_modes=("owned",),
        case_ids=DELEGATED_CASE_IDS,
    )


def resolve_candidates(
    profile: ValidationProfile,
    candidates: Sequence[ApplicableTarget],
    *,
    explicit: bool,
) -> tuple[ResolvedScenario, ...]:
    """Reuse shared-IdP edge binding and add exact package fixture inputs."""

    shared = resolve_shared_idp_candidates(profile, candidates, explicit=explicit)
    if not shared:
        return ()
    config = delegated_package_config()
    return tuple(_with_package_config(item, config) for item in shared)


def install_requirements_for(
    selected: Sequence[ResolvedScenario],
) -> dict[str, Any]:
    """Publish the same issuer trust requirement as the shared-IdP provider."""

    return install_requirements(selected)


def delegated_package_config() -> DelegatedPackageConfig:
    """Read and validate configured receiver package fixtures.

    The provider never accepts repository credentials or arbitrary requirement
    expressions.  The receiver owns the private catalog; the selection carries
    only exact, public name/version coordinates and import names.
    """

    packages = _environment_string_list(_PACKAGES_ENV)
    imports = _environment_string_list(_IMPORTS_ENV)
    return _build_package_config(packages, imports)


def package_config_from_values(values: Mapping[str, Any]) -> DelegatedPackageConfig:
    """Validate package inputs recovered from an untrusted plan/state object."""

    packages = values.get("python_packages")
    imports = values.get("package_imports")
    if not isinstance(packages, (list, tuple)) or not isinstance(
        imports, (list, tuple)
    ):
        raise ProviderContractError("delegated package fixture inputs are missing")
    try:
        return _build_package_config(packages, imports)
    except (TypeError, ValueError) as exc:
        raise ProviderContractError(str(exc)) from None


def _with_package_config(
    selected: ResolvedScenario, config: DelegatedPackageConfig
) -> ResolvedScenario:
    parameters = {
        **selected.redacted_parameters,
        "python_packages": list(config.coordinates),
        "package_imports": list(config.import_names),
        "expected_package_versions": dict(config.expected_versions),
        "fixture_mode": "owned",
    }
    return selected.model_copy(
        update={
            "scenario_id": DELEGATED_SCENARIO_ID,
            "case_ids": DELEGATED_CASE_IDS,
            "redacted_parameters": parameters,
        }
    )


def _environment_string_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        raise ProviderContractError("delegated package fixture is not configured")
    return _parse_environment_list(raw, name)


def _parse_environment_list(raw: str, name: str) -> tuple[str, ...]:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderContractError(f"{name} must contain a JSON string list") from exc
    if not isinstance(values, list):
        raise ProviderContractError(f"{name} must contain only strings")
    if any(not isinstance(value, str) for value in values):
        raise ProviderContractError(f"{name} must contain only strings")
    return tuple(value.strip() for value in values)


def _build_package_config(
    packages: Sequence[object], imports: Sequence[object]
) -> DelegatedPackageConfig:
    coordinates = _normalized_packages(packages)
    _require_minimum_packages(coordinates)
    _require_matching_fixture_lengths(coordinates, imports)
    import_names = tuple(_validated_import_name(value) for value in imports)
    return DelegatedPackageConfig(
        coordinates,
        import_names,
        _expected_versions(coordinates),
    )


def _normalized_packages(packages: Sequence[object]) -> tuple[str, ...]:
    package_values = [value for value in packages if isinstance(value, str)]
    if len(package_values) != len(packages):
        raise ProviderContractError(
            "delegated package fixture requires string package coordinates"
        )
    try:
        return normalize_python_packages(package_values)
    except (TypeError, ValueError) as exc:
        raise ProviderContractError(
            f"delegated package fixture requires exact name==version coordinates: {exc}"
        ) from None


def _require_minimum_packages(coordinates: Sequence[str]) -> None:
    if len(coordinates) < 2:
        raise ProviderContractError(
            "delegated workload edge requires at least two dependencies"
        )


def _require_matching_fixture_lengths(
    coordinates: Sequence[str], imports: Sequence[object]
) -> None:
    if len(coordinates) != len(imports):
        raise ProviderContractError(
            "delegated package and import lists must have equal lengths"
        )


def _expected_versions(coordinates: Sequence[str]) -> dict[str, str]:
    return dict(coordinate.split("==", maxsplit=1) for coordinate in coordinates)


def _validated_import_name(value: object) -> str:
    if not isinstance(value, str) or _IMPORT_NAME.fullmatch(value.strip()) is None:
        raise ProviderContractError(
            "delegated package fixture contains an invalid import name"
        )
    return value.strip()
