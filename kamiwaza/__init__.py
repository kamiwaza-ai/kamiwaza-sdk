"""Kamiwaza SDK — top-level customer-facing package.

Per design §4.2.11 of the Federation API + SDK MVP, the customer-facing
Python SDK lives under the `kamiwaza` namespace (distinct from the legacy
`kamiwaza_sdk` and `kamiwaza_client` packages also distributed in this
wheel). The federation-aware client surface ships from this package.

Recommended customer entry point:

    from kamiwaza import Kamiwaza
    client = Kamiwaza.from_env()
    client.federations.pair("ORION", role="initiator", remote_url=...)
    client.jobs.run(target_cluster="ORION", entrypoint=...)

Implementation lands across multiple WS-M1 milestone tickets:
    - T5.1 (this ticket): package skeleton, base exception, class shell
    - T5.2: Kamiwaza class — from_env, httpx setup, retry middleware
    - T5.3: federations module
    - T5.9: jobs module
    - T5.10: typed exception hierarchy
    - T5.11: Pydantic skeleton models
"""

from __future__ import annotations

from kamiwaza.client import Kamiwaza as Kamiwaza
from kamiwaza.exceptions import KamiwazaError as KamiwazaError
from kamiwaza.models import BrokeredUser as BrokeredUser
from kamiwaza.models import Federation as Federation
from kamiwaza.models import JobResult as JobResult

__all__ = [
    "BrokeredUser",
    "Federation",
    "JobResult",
    "Kamiwaza",
    "KamiwazaError",
]
