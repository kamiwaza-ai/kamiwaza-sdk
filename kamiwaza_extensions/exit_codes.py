"""Exit codes for the kz-ext CLI.

Canonical mapping of UAC-9d runtime-lib exception classes to process
exit codes, plus standard CLI and cluster/registry failure codes.

Design reference: §4.2.8 `DoctorUACFailureHints` + `ExitCodeMap`.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    FAILURE = 1
    VALIDATION = 2

    # UAC-9d runtime-lib exception classes
    MISBOUND_AUTH = 10
    UNEXPECTED_CONTEXT = 11
    OUT_OF_ENVELOPE_ACCESS = 12
    PLATFORM_OUTAGE = 13

    # Cluster and registry failures
    REGISTRY_AUTH = 20
    CLUSTER_UNREACHABLE = 21
    CRD_PATCH_UNSUPPORTED = 22
    CLUSTER_NOT_READY = 23
