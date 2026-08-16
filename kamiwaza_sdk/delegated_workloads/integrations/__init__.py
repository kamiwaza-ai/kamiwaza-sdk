"""Framework adapters for the neutral protected-resource guard."""

from kamiwaza_sdk.delegated_workloads.integrations.asgi import (
    DelegatedResourceASGI as DelegatedResourceASGI,
)

__all__ = ("DelegatedResourceASGI",)
