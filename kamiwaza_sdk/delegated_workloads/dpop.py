"""Compatibility imports for delegated DPoP primitives.

New code should import the complete workload proof lifecycle from ``proof``.
"""

from kamiwaza_sdk.delegated_workloads.proof import DPoPKeyLifecycle
from kamiwaza_sdk.delegated_workloads.proof import DPoPProofKey as DPoPProofKey
from kamiwaza_sdk.delegated_workloads.proof import (
    DPoPProofRequest as DPoPProofRequest,
)
from kamiwaza_sdk.delegated_workloads.proof import body_digest as body_digest

__all__ = ("DPoPKeyLifecycle", "DPoPProofKey", "DPoPProofRequest", "body_digest")
