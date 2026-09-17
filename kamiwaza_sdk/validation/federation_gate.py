"""Exact artifact identity for the SDK-owned federation gate fixture."""

import re
from typing import Any

from kamiwaza_sdk.validation.federation_fixture import GATE_CLASSPATH, GATE_PACKAGE_NAME
from kamiwaza_sdk.validation.provider import ProviderContractError


def expected_gate_package(package_spec: str, hash_digest: str) -> dict[str, str]:
    """Require an explicit artifact digest even when reusing an installed fixture."""
    name, separator, version = package_spec.partition("==")
    if not version:
        raise ProviderContractError("gate fixture requires an exact version")
    if (name, separator) != (GATE_PACKAGE_NAME, "=="):
        raise ProviderContractError(
            "gate fixture requires an exact acme-gates==version spec"
        )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", hash_digest) is None:
        raise ProviderContractError(
            "gate fixture requires a configured expected sha256 hash"
        )
    return {
        "name": name,
        "package_spec": package_spec,
        "version": version,
        "hash_digest": hash_digest,
        "status": "active",
    }


def validate_gate_package(package: Any, expected: dict[str, str]) -> None:
    """Reject an incomplete or different artifact before discovery or reuse."""
    mismatches = [
        field
        for field, value in expected.items()
        if getattr(package, field, None) != value
    ]
    if GATE_CLASSPATH not in (getattr(package, "classpaths", None) or []):
        mismatches.append("classpaths")
    if mismatches:
        raise ProviderContractError(
            f"Incompatible gate fixture: {', '.join(mismatches)} do not match "
            f"{expected['package_spec']}. On an isolated test cluster, remove old "
            "fixture bindings and uninstall the incompatible package before preparing "
            "the requested artifact. No replacement or uninstall was attempted."
        )
