"""Schema integrity tests for the D210 scenario runbooks.

Always-on (no staging dependency). Enforces the contract from system-design
§4.2.15: 5 runbooks exist, schema is well-formed, sign-off actors and UAC
references are present, the harness can load each one without raising.
"""

from __future__ import annotations

import re

import pytest

from tests.e2e.scenarios.harness import (
    REQUIRED_RUNBOOK_FIELDS,
    REQUIRED_STEP_FIELDS,
    SIGN_OFF_TEMPLATE,
    all_runbook_paths,
    load_runbook,
)

EXPECTED_SCENARIO_IDS = {"S1", "S2", "S3", "S4", "S5"}
PRESTON_SIGN_OFF = {"S1", "S3", "S4"}
UAC_RE = re.compile(r"^UAC-\d+[a-z]?$")


@pytest.mark.unit
def test_all_five_scenarios_have_a_runbook():
    paths = list(all_runbook_paths())
    assert len(paths) == 5, (
        f"expected 5 Appendix A runbooks under tests/e2e/scenarios/runbooks/, "
        f"found {len(paths)}: {[p.name for p in paths]}"
    )
    ids = {load_runbook(sid)["id"] for sid in EXPECTED_SCENARIO_IDS}
    assert ids == EXPECTED_SCENARIO_IDS


@pytest.mark.unit
@pytest.mark.parametrize("scenario_id", sorted(EXPECTED_SCENARIO_IDS))
def test_runbook_schema_is_well_formed(scenario_id):
    runbook = load_runbook(scenario_id)
    for field in REQUIRED_RUNBOOK_FIELDS:
        assert field in runbook, f"{scenario_id}: missing {field!r}"
    assert (
        isinstance(runbook["uacs"], list) and runbook["uacs"]
    ), f"{scenario_id}: uacs must be a non-empty list"
    for uac in runbook["uacs"]:
        assert UAC_RE.match(uac), f"{scenario_id}: malformed UAC ref {uac!r}"
    for i, step in enumerate(runbook["steps"]):
        for field in REQUIRED_STEP_FIELDS:
            assert field in step, f"{scenario_id}.steps[{i}]: missing {field!r}"


@pytest.mark.unit
@pytest.mark.parametrize("scenario_id", sorted(PRESTON_SIGN_OFF))
def test_preston_signs_off_s1_s3_s4(scenario_id):
    """S1, S3, S4 require Preston's manual sign-off per design §6.2 M3 / PRD §EDX-E2E-1."""
    runbook = load_runbook(scenario_id)
    assert (
        "preston" in runbook["sign_off_actor"].lower()
    ), f"{scenario_id}: sign_off_actor must be Preston (got {runbook['sign_off_actor']!r})"


@pytest.mark.unit
@pytest.mark.parametrize(
    "scenario_id", sorted(EXPECTED_SCENARIO_IDS - PRESTON_SIGN_OFF)
)
def test_sdk_team_owns_s2_s5(scenario_id):
    runbook = load_runbook(scenario_id)
    assert "sdk" in runbook["sign_off_actor"].lower(), (
        f"{scenario_id}: sign_off_actor must be the SDK team for automated scenarios "
        f"(got {runbook['sign_off_actor']!r})"
    )


@pytest.mark.unit
def test_sign_off_template_has_all_placeholders():
    assert SIGN_OFF_TEMPLATE.exists(), f"sign-off template missing: {SIGN_OFF_TEMPLATE}"
    text = SIGN_OFF_TEMPLATE.read_text()
    for placeholder in (
        "{{SCENARIO_ID}}",
        "{{SCENARIO_NAME}}",
        "{{SIGN_OFF_ACTOR}}",
        "{{RUN_DATE}}",
        "{{CI_JOB_URL}}",
        "{{DURATION_S}}",
        "{{STEPS_TABLE}}",
    ):
        assert (
            placeholder in text
        ), f"sign-off template missing placeholder {placeholder}"


@pytest.mark.unit
def test_uac_16_referenced_by_every_scenario():
    """UAC-16 is the umbrella full-loop UAC; every scenario must trace to it."""
    for scenario_id in sorted(EXPECTED_SCENARIO_IDS):
        runbook = load_runbook(scenario_id)
        assert (
            "UAC-16" in runbook["uacs"]
        ), f"{scenario_id}: must reference UAC-16 (full-loop umbrella)"
