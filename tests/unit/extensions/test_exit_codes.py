"""Tests for kamiwaza_extensions.exit_codes.

Traces to: ENG-3885 (UAC-9d CLI plumbing), design §4.2.8 ExitCodeMap.
"""

import pytest


@pytest.mark.unit
class TestExitCode:
    # TS-1: ExitCode enum has all required values (0/1/2, 10-13, 20-23)
    def test_standard_codes(self):
        from kamiwaza_extensions.exit_codes import ExitCode

        assert ExitCode.OK == 0
        assert ExitCode.FAILURE == 1
        assert ExitCode.VALIDATION == 2

    def test_uac_9d_codes(self):
        from kamiwaza_extensions.exit_codes import ExitCode

        assert ExitCode.MISBOUND_AUTH == 10
        assert ExitCode.UNEXPECTED_CONTEXT == 11
        assert ExitCode.OUT_OF_ENVELOPE_ACCESS == 12
        assert ExitCode.PLATFORM_OUTAGE == 13

    def test_cluster_and_registry_codes(self):
        from kamiwaza_extensions.exit_codes import ExitCode

        assert ExitCode.REGISTRY_AUTH == 20
        assert ExitCode.CLUSTER_UNREACHABLE == 21
        assert ExitCode.CRD_PATCH_UNSUPPORTED == 22
        assert ExitCode.CLUSTER_NOT_READY == 23


@pytest.mark.extension_regression
@pytest.mark.unit
class TestExitCodeFor:
    # TS-2: exit_code_for('misbound_auth')=10; same for all 4 UAC-9d classes
    def test_misbound_auth(self):
        from kamiwaza_extensions.exit_codes import ExitCode, exit_code_for

        assert exit_code_for("misbound_auth") == ExitCode.MISBOUND_AUTH

    def test_unexpected_context(self):
        from kamiwaza_extensions.exit_codes import ExitCode, exit_code_for

        assert exit_code_for("unexpected_context") == ExitCode.UNEXPECTED_CONTEXT

    def test_out_of_envelope_access(self):
        from kamiwaza_extensions.exit_codes import ExitCode, exit_code_for

        assert exit_code_for("out_of_envelope_access") == ExitCode.OUT_OF_ENVELOPE_ACCESS

    def test_platform_outage(self):
        from kamiwaza_extensions.exit_codes import ExitCode, exit_code_for

        assert exit_code_for("platform_outage") == ExitCode.PLATFORM_OUTAGE

    def test_unknown_class_returns_failure(self):
        from kamiwaza_extensions.exit_codes import ExitCode, exit_code_for

        assert exit_code_for("some_unknown_class") == ExitCode.FAILURE
