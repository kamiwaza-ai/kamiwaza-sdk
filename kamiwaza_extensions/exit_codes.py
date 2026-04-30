"""Exit codes for the kz-ext CLI.

Canonical mapping of UAC-9d runtime-lib exception classes to process
exit codes, plus standard CLI and cluster/registry failure codes.

Design reference: §4.2.8 `DoctorUACFailureHints` + `ExitCodeMap`.
"""

from __future__ import annotations

import json
from enum import IntEnum
from functools import lru_cache
from importlib import resources


class ExitCode(IntEnum):
    OK = 0
    FAILURE = 1
    VALIDATION = 2

    # UAC-9d runtime-lib exception classes
    MISBOUND_AUTH = 10
    UNEXPECTED_CONTEXT = 11
    OUT_OF_ENVELOPE_ACCESS = 12
    PLATFORM_OUTAGE = 13
    STREAM_INTERRUPTED = 14  # PR-86 H5 — added with the runtime-lib class

    # Cluster and registry failures
    REGISTRY_AUTH = 20
    CLUSTER_UNREACHABLE = 21
    CRD_PATCH_UNSUPPORTED = 22
    CLUSTER_NOT_READY = 23


@lru_cache(maxsize=1)
def _exception_name_to_exit_code() -> dict[str, int]:
    """Load class-name → exit-code map from the runtime lib's canonical JSON."""
    data = json.loads(
        resources.files("kamiwaza_extensions_lib")
        .joinpath("exception_names.json")
        .read_text(encoding="utf-8")
    )
    return {entry["name"]: entry["exit_code"] for entry in data["classes"]}


def exit_code_for(class_name: str) -> ExitCode:
    """Return the ExitCode for a runtime-lib exception ``class_name``.

    Unknown class names fall back to ``ExitCode.FAILURE``.
    """
    code = _exception_name_to_exit_code().get(class_name)
    return ExitCode(code) if code is not None else ExitCode.FAILURE
