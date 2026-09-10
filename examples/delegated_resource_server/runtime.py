"""Environment and bounded Core JWKS wiring for the resource example."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

from kamiwaza_sdk.delegated_workloads.transport import SessionPort


_PROTOCOL_PATH = "/api/v1/delegated-workloads"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceRuntimeConfig:
    core_base_url: str
    audience: str
    registration_revision_id: UUID

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str] | None = None,
    ) -> ResourceRuntimeConfig:
        source = values or os.environ
        try:
            return cls(
                core_base_url=source["KAMIWAZA_DELEGATED_CORE_URL"],
                audience=source["RESOURCE_AUDIENCE"],
                registration_revision_id=UUID(
                    source["RESOURCE_REGISTRATION_REVISION_ID"]
                ),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(
                "neutral resource runtime configuration is invalid"
            ) from exc

    def __post_init__(self) -> None:
        core = urlsplit(self.core_base_url)
        audience = urlsplit(self.audience)
        valid = (
            core.scheme == "https",
            bool(core.netloc),
            core.path.rstrip("/") == _PROTOCOL_PATH,
            audience.scheme == "https",
            bool(audience.netloc),
            audience.path in ("", "/"),
            not audience.query,
            not audience.fragment,
        )
        if not all(valid):
            raise ValueError("neutral resource runtime configuration is invalid")


class BoundedJwksProvider:
    """Refresh public capability keys on a short, fail-closed cache bound."""

    def __init__(
        self,
        session: SessionPort,
        url: str,
        lifetime: timedelta = timedelta(seconds=30),
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("JWKS cache lifetime must be positive")
        self._session = session
        self._url = url
        self._lifetime = lifetime
        self._document: Mapping[str, object] | None = None
        self._valid_until: datetime | None = None

    def __call__(self, now: datetime) -> Mapping[str, object]:
        if self._current(now):
            return cast(Mapping[str, object], self._document)
        response = self._session.request("GET", self._url)
        if response.status_code != 200:
            raise ValueError("delegated capability keys are unavailable")
        document = _jwks_document(response.json())
        self._document = document
        self._valid_until = now + self._lifetime
        return document

    def _current(self, now: datetime) -> bool:
        return self._document is not None and bool(
            self._valid_until and now < self._valid_until
        )


def _jwks_document(value: Any) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("delegated capability keys are invalid")
    keys = value.get("keys")
    if not isinstance(keys, list):
        raise ValueError("delegated capability keys are invalid")
    return cast(Mapping[str, object], value)


__all__ = ("BoundedJwksProvider", "ResourceRuntimeConfig")
