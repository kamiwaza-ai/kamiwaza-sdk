"""Framework-neutral public boundary for protected resource servers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar


_HandlerT = TypeVar("_HandlerT", bound=Callable[..., object])


class DelegatedResourceServer(Protocol):
    """Guard a handler through the active platform resource contract."""

    def guard(
        self,
        registration: Mapping[str, object],
        handler: _HandlerT,
    ) -> _HandlerT: ...


__all__ = ("DelegatedResourceServer",)
