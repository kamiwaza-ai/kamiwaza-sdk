"""The full per-operation catalog, categorised and filterable.

The escape hatch, not the default. A host that runs its own code mode asks for
this deliberately and pays for it knowingly; the fixed discovery surface is what
every other host uses (FR-001, FR-028).

Its cost is the reason it is not the default. Per-entry cost is measured rather
than asserted — :func:`measure_cost` counts with the caller's own tokeniser, so
the number in the documentation can be reproduced instead of trusted.

Protocol-neutral: an entry is a plain mapping, and the consuming server maps it
onto whatever its protocol revision calls a tool definition.

**An entry comes at three levels of detail**, because the whole catalog is not
the only thing a host might want from it. ``names`` is the identifiers alone,
``brief`` adds the category and the description, and ``full`` is every field.
Measured over 336 published operations with ``cl100k_base``: 2,306 tokens,
8,338, and 22,535 — the cheapest level costs a tenth of the whole catalog. The tiers are Anthropic's documented pattern for a large
tool surface — a detail level that returns "name only, name and description,
or the full definition with schemas" — and the shape Stripe's MCP server
ships, where ``api_search`` finds an endpoint, ``api_details`` returns one
endpoint's schema, and the call tools use it. A host reading the catalog to
pick a name never has to pay for every parameter list to do it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from .descriptors import (
    OperationDescriptor,
    describe_all,
    resolve_description,
    resolve_service,
)
from .spec_index import OperationIndex

__all__ = [
    "DETAIL_LEVELS",
    "Detail",
    "CatalogEntry",
    "build_catalog",
    "categories",
    "measure_cost",
]

#: How much of an entry a caller wants. ``full`` is the default everywhere, so
#: a host that asks for the catalog the way it always has receives what it
#: always received: a cheaper default would change what an existing caller
#: gets without it asking.
Detail = Literal["names", "brief", "full"]

#: The levels, cheapest first, for a surface that has to name them to a caller.
DETAIL_LEVELS: tuple[Detail, ...] = ("names", "brief", "full")

#: Service attribute to catalog category. A category groups operations the way a
#: caller thinks about them, which is not always how the client is structured:
#: `models`, `serving` and `catalog` are three services and one job.
_CATEGORIES: dict[str, str] = {
    "models": "models",
    "serving": "models",
    "catalog": "models",
    "embedding": "models",
    "openai": "models",
    "datasets": "data",
    "ingestion": "data",
    "retrieval": "data",
    "enclaves": "data",
    # Nested sub-clients. A selector is `service.subclient`, and a family can
    # belong to a different category than its parent: catalog is grouped with
    # models, but its secrets are an access concern.
    "catalog.containers": "data",
    "catalog.datasets": "data",
    "catalog.secrets": "access",
    "enclaves.connectors": "data",
    "enclaves.documents": "data",
    "gates.packages": "access",
    "context": "data",
    "cluster": "infrastructure",
    "federations": "infrastructure",
    "jobs": "infrastructure",
    "lab": "infrastructure",
    "auth": "access",
    "authz": "access",
    "subjects": "access",
    "gates": "access",
    "workrooms": "collaboration",
    "activity": "collaboration",
    "conversations": "collaboration",
    "prompts": "agents",
    "agents": "agents",
    "skills": "agents",
    "apps": "extensions",
    "extensions": "extensions",
    "connectors": "extensions",
    "tools": "extensions",
    # An extension's own operator surface, grouped by who owns it rather than
    # by what it configures: `kaizen_ops` binds model roles on a Kaizen
    # instance, so it reads like a models entry, but a host filtering for
    # `models` wants the platform's own model operations and not one
    # extension's settings.
    "kaizen_ops": "extensions",
}

#: Category used when a service has no mapping, so a service added to the client
#: appears in the catalog immediately rather than waiting for this table.
_UNCATEGORISED = "other"


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One operation as an agent host would see it in the full catalog.

    Attributes:
        published_id: Identifier the host calls.
        selector: Internal dotted identifier, carried so a report can be traced
            back to the client method.
        category: Which group this operation belongs to.
        description: Resolved description, or ``None`` when no source has one.
        requires_approval: Whether a member must approve before it runs.
        read_only: Whether it leaves platform state unchanged.
        destructive: Whether it removes something or ends its life.
        idempotent: Whether repeating it is equivalent to calling it once.
        open_world: Whether it reaches a system outside this platform.
        parameters: Parameter names in declaration order.
        required_parameters: The subset with no default.
    """

    published_id: str
    selector: str
    category: str
    description: str | None
    requires_approval: bool
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    parameters: tuple[str, ...]
    required_parameters: tuple[str, ...]

    def as_definition(self, detail: Detail = "full") -> dict[str, Any]:
        """Return the entry as a plain mapping for a transport to carry.

        Keys are the neutral names this layer owns. Mapping them onto a protocol's
        own field names is the consuming server's job, because those names change
        with the protocol revision and this package must not.

        Every level is a mapping carrying ``id``, so a host parses one element
        type whichever level it asked for. A bare list of identifiers would be
        1,632 tokens against this level's 2,306, and changing what an element
        *is* with a query parameter is a worse contract than 674 tokens buys.

        Args:
            detail: How much of the entry to return. ``names`` is the
                identifier, ``brief`` adds the category and description, and
                ``full`` is every field.

        Returns:
            The entry at that level of detail.

        Raises:
            ValueError: If ``detail`` is not one of :data:`DETAIL_LEVELS`.
        """
        if detail not in DETAIL_LEVELS:
            raise ValueError(
                f"detail must be one of {', '.join(DETAIL_LEVELS)}, not {detail!r}"
            )
        named = {"id": self.published_id}
        if detail == "names":
            return named
        brief = {**named, "category": self.category, "description": self.description}
        if detail == "brief":
            return brief
        return {
            **brief,
            "requires_approval": self.requires_approval,
            "hints": {
                "read_only": self.read_only,
                "destructive": self.destructive,
                "idempotent": self.idempotent,
                "open_world": self.open_world,
            },
            "parameters": list(self.parameters),
            "required": list(self.required_parameters),
        }


def _category_for(service: str) -> str:
    """Return the catalog category for a service attribute name.

    Args:
        service: Service attribute on the client.

    Returns:
        The mapped category, or ``"other"`` when the service is not yet mapped.
    """
    return _CATEGORIES.get(service, _UNCATEGORISED)


def _entry_for(descriptor: OperationDescriptor, client: Any) -> CatalogEntry:
    """Build one catalog entry from a descriptor.

    Args:
        descriptor: The described operation.
        client: Client the index was built from, used to resolve the description.

    Returns:
        The catalog entry.
    """
    service = resolve_service(client, descriptor.entry.service)
    description, _ = resolve_description(descriptor.entry, service)
    return CatalogEntry(
        published_id=descriptor.published_id,
        selector=descriptor.selector,
        category=_category_for(descriptor.entry.service),
        description=description,
        requires_approval=descriptor.requires_approval,
        read_only=descriptor.hints.read_only,
        destructive=descriptor.hints.destructive,
        idempotent=descriptor.hints.idempotent,
        open_world=descriptor.hints.open_world,
        parameters=descriptor.entry.parameters,
        required_parameters=descriptor.entry.required_parameters,
    )


def build_catalog(
    index: OperationIndex,
    client: Any,
    *,
    category: str | None = None,
    read_only: bool | None = None,
) -> tuple[CatalogEntry, ...]:
    """Build the full catalog, optionally filtered.

    Filters exist so a host that wants the catalog does not have to take all of
    it: asking for one category is the difference between a few hundred entries
    and a few dozen.

    Args:
        index: The operation index.
        client: Client the index was built from.
        category: Keep only this category when given.
        read_only: Keep only read-only operations when ``True``, only mutations
            when ``False``, everything when ``None``.

    Returns:
        Entries in index order. Unpublished operations never appear.
    """
    entries = (_entry_for(descriptor, client) for descriptor in describe_all(index))
    if category is not None:
        entries = (entry for entry in entries if entry.category == category)
    if read_only is not None:
        entries = (entry for entry in entries if entry.read_only is read_only)
    return tuple(entries)


def categories(entries: Iterable[CatalogEntry]) -> dict[str, int]:
    """Count catalog entries per category.

    Args:
        entries: Catalog entries.

    Returns:
        Mapping from category to entry count, largest first.
    """
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.category] = counts.get(entry.category, 0) + 1
    return dict(sorted(counts.items(), key=lambda row: (-row[1], row[0])))


def measure_cost(
    entries: Iterable[CatalogEntry],
    count_tokens: Callable[[str], int],
    detail: Detail = "full",
) -> dict[str, int]:
    """Measure the catalog's token cost with a caller-supplied tokeniser.

    The tokeniser is an argument rather than an import: this package must not
    acquire a tokeniser dependency for a measurement, and the number is only
    meaningful for the model the caller is actually budgeting against.

    **This prices the entries, not a response carrying them.** The entries are
    measured in the compact encoding a JSON transport sends — no spaces, which
    is what ``starlette.responses.JSONResponse`` emits and what this counts —
    so a server's own envelope of counts and filters is outside the number. On
    the 336-operation surface that envelope is 80 tokens against 22,535, and a
    caller budgeting a context window wants the part that scales with the
    surface.

    Args:
        entries: Catalog entries to measure.
        count_tokens: Function returning the token count of a string. Typically
            ``lambda text: len(encoding.encode(text))``.
        detail: The level being priced. A cost measured at one level does not
            describe another, so this has to match what is being returned.

    Returns:
        Mapping with ``entries``, ``total`` tokens, and ``per_entry`` as the
        integer mean, so a cost can be projected onto a larger surface.
    """
    materialised = tuple(entries)
    total = sum(
        count_tokens(json.dumps(entry.as_definition(detail), separators=(",", ":")))
        for entry in materialised
    )
    return {
        "entries": len(materialised),
        "total": total,
        "per_entry": total // len(materialised) if materialised else 0,
    }
