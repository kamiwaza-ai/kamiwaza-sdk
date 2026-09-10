"""JSON representation parity for exact scenario matcher operands."""

from __future__ import annotations

import pytest

from kamiwaza_sdk.validation import ValidationProfile
from kamiwaza_sdk.validation.applicability import applicable_targets
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import ProviderContractError

from .support import profile_payload

pytestmark = pytest.mark.contract


def _profile() -> ValidationProfile:
    return ValidationProfile.model_validate(profile_payload())


def _matches(path: tuple[str, ...], operator: str, value: object) -> bool:
    source = GoldenProvider().describe()[0]
    matcher = source.applies_when[0].model_copy(
        update={"path": path, "operator": operator, "value": value}
    )
    descriptor = source.model_copy(
        update={"target_scope": "cluster", "applies_when": (matcher,)}
    )
    return bool(applicable_targets(_profile(), descriptor))


@pytest.mark.parametrize(
    ("path", "operator", "value"),
    [
        (("cluster", "roles"), "eq", ["controller", "inference"]),
        (
            ("cluster", "roles"),
            "in",
            [["worker"], ["controller", "inference"]],
        ),
        (
            ("cluster", "hardware"),
            "eq",
            {
                "accelerators": [
                    {"vendor": "amd", "architecture": "gfx1151", "count": 1}
                ]
            },
        ),
        (
            ("cluster", "hardware"),
            "in",
            [
                {"accelerators": []},
                {
                    "accelerators": [
                        {
                            "vendor": "amd",
                            "architecture": "gfx1151",
                            "count": 1,
                        }
                    ]
                },
            ],
        ),
        (("cluster", "node_count"), "eq", 1.0),
        (("cluster", "node_count"), "in", [2.0, 1.0]),
    ],
)
def test_exact_matchers_accept_equivalent_json_representations(
    path: tuple[str, ...], operator: str, value: object
) -> None:
    assert _matches(path, operator, value)


@pytest.mark.parametrize("value", [True, "1"])
def test_numeric_matchers_reject_cross_category_coercion(value: object) -> None:
    with pytest.raises(ProviderContractError, match="incompatible value types"):
        _matches(("cluster", "node_count"), "eq", value)
