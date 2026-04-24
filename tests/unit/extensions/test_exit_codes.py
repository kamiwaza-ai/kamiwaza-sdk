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
