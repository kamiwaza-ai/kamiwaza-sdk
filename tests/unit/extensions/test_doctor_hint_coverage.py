"""Contract test: every UAC-9d class has a doctor hint + exit-code mapping.

Traces to: ENG-3885 (§4.2.8 DoctorUACFailureHints).

This test is the canonical coverage check for the exception_names.json
source of truth — it enforces the 1:1 invariant between runtime-lib
class names, CLI exit codes, and doctor reference entries.
"""

import json
from importlib import resources

import pytest


@pytest.fixture
def exception_classes() -> list[dict]:
    """Load the canonical list of UAC-9d exception classes."""
    raw = (
        resources.files("kamiwaza_extensions_lib")
        .joinpath("exception_names.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)["classes"]


@pytest.mark.unit
class TestDoctorHintCoverage:
    # TS-3: every class in exception_names.json has a CheckResult + ExitCode
    def test_exit_code_mapping_complete(self, exception_classes):
        from kamiwaza_extensions.exit_codes import exit_code_for

        for entry in exception_classes:
            assert int(exit_code_for(entry["name"])) == entry["exit_code"], (
                f"exit_code_for({entry['name']!r}) does not match "
                f"exception_names.json (expected {entry['exit_code']})"
            )

    def test_doctor_surfaces_hint_for_each_class(self, exception_classes):
        from kamiwaza_extensions.doctor import DoctorChecker

        results = DoctorChecker().run_all()
        names = {r.name for r in results}
        messages_by_name = {r.name: (r.message, r.fix) for r in results}

        for entry in exception_classes:
            expected_name = f"Failure class: {entry['name']}"
            assert expected_name in names, (
                f"Doctor missing CheckResult for class {entry['name']!r} "
                f"(expected a result named {expected_name!r})"
            )
            # Hint text must appear somewhere in the CheckResult
            message, fix = messages_by_name[expected_name]
            hint = entry["doctor_hint"]
            assert hint in message or hint in (fix or ""), (
                f"Doctor hint for {entry['name']!r} missing the canonical "
                f"text from exception_names.json"
            )
