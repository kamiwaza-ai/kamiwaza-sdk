"""The operation index over this client's callable method surface.

Built from the client itself, not from the OpenAPI document. Roughly a quarter
of the client's methods have no entry in that document — composites, federation
helpers, retrieval flight paths — and an index that omitted them could not
promise an agent it reaches what the platform can do (FR-005). The document
remains the schema source where an operation has one, and the subject of the
staleness gate (FR-005a).

The index counts itself. Nothing here hardcodes how many operations exist,
because a method added to the client must appear the day it ships with no
curation step and no release of the consuming server (FR-005c).
"""

from __future__ import annotations

import re

import hashlib
import inspect
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .ids import UNPUBLISHED, UnpublishedOperationError, published_id, selector

__all__ = [
    "DESCRIPTOR_VERSION",
    "OperationEntry",
    "OperationIndex",
    "build_index",
    "first_sentence",
]

#: Version of the descriptor shape itself, distinct from the SDK version.
#: A consumer pins the SDK exactly; this tells it what shape to expect.
DESCRIPTOR_VERSION = "1"

_FIRST_SENTENCE_ENDINGS = (". ", ".\n", ".")


def first_sentence(text: str | None) -> str | None:
    """Return the first sentence of a docstring, or ``None`` when there is none.

    Args:
        text: Raw docstring, possibly multi-paragraph, possibly ``None``.

    Returns:
        The first sentence with surrounding whitespace collapsed, or ``None``
        when the docstring is absent or blank.
    """
    if not text:
        return None
    flat = " ".join(text.strip().split())
    if not flat:
        return None
    for ending in _FIRST_SENTENCE_ENDINGS:
        head, sep, _ = flat.partition(ending)
        if sep:
            return f"{head}."
    return flat


@dataclass(frozen=True, slots=True)
class OperationEntry:
    """One callable operation on the client.

    Attributes:
        selector: Internal dotted ``service.method`` identifier.
        published_id: Identifier an agent host sees, verb first in snake_case.
        service: Client attribute the operation lives on.
        method: Method name on that service.
        summary: First sentence of the method docstring, or ``None`` when the
            method has no docstring. A ``None`` here is what the docstring gate
            fails on; the index records the absence rather than inventing text.
        parameters: Parameter names in declaration order, excluding ``self``.
        required_parameters: Subset of ``parameters`` with no default.
        returns: Rendered return annotation, or ``None`` when unannotated.
        unpublished_reason: Why the operation is withheld, or ``None``.
    """

    selector: str
    published_id: str
    service: str
    method: str
    summary: str | None
    parameters: tuple[str, ...]
    required_parameters: tuple[str, ...]
    returns: str | None
    unpublished_reason: str | None = None

    @property
    def is_published(self) -> bool:
        """Whether this operation is offered to agents."""
        return self.unpublished_reason is None

    def search_text(self) -> str:
        """Return the text keyword search matches against.

        Joins the identifiers and the summary, so a search for a platform noun
        finds an operation whose docstring mentions it even when the method name
        does not.
        """
        parts = [self.published_id, self.selector, self.summary or ""]
        return " ".join(parts).lower()


#: Weight for a term found in an identifier, versus one found only in a summary.
#: An identifier match is the stronger signal: a caller searching "deploy" wants
#: the deploy operations before the ones that merely mention deployment.
_IDENTIFIER_WEIGHT = 3
_SUMMARY_WEIGHT = 1

#: Shortest term that carries signal on its own.
#:
#: Measured: "deploy a model" matched 325 of 332 published operations, because
#: "a" appears as a substring in nearly every summary. A one- or two-character
#: term is noise when a longer one is present, so it is dropped rather than
#: counted — and kept when it is all the caller gave, since "id" should still
#: search for something.
_SHORTEST_MEANINGFUL_TERM = 3


def _matches(term: str, text: str) -> bool:
    """Whether a term appears in text at the start of a word.

    A bare substring test makes "model" match "remodelled" and "get" match
    "widget". Anchoring to a word start keeps the prefix behaviour a searcher
    expects — "deploy" still finds "deployment" — without matching the middle
    of an unrelated word. Identifiers are split on underscores and dots by the
    same rule: a word boundary sits either side of an underscore or a dot.

    Args:
        term: An already-lowercased search term.
        text: Already-lowercased text to search.

    Returns:
        Whether the term starts a word in the text.
    """
    return re.search(rf"\b{re.escape(term)}", text) is not None


def meaningful_terms(query: str) -> list[str]:
    """The terms a query is actually searched on.

    Args:
        query: The caller's words.

    Returns:
        Lowercased terms, with the noise-length ones dropped when longer terms
        remain. Empty for an empty query, which the caller reports as nothing
        found rather than matching everything.
    """
    terms = [t for t in query.lower().split() if t]
    longer = [t for t in terms if len(t) >= _SHORTEST_MEANINGFUL_TERM]
    return longer or terms


def _score(entry: OperationEntry, terms: list[str]) -> int:
    """Score one operation against already-lowercased search terms.

    Deliberately boring: no embeddings until a measured miss justifies the
    dependency and the index-build cost.

    Args:
        entry: The operation to score.
        terms: Lowercased search terms.

    Returns:
        The total score. Zero means no term matched, and the caller drops it.
    """
    identifiers = f"{entry.published_id} {entry.selector}".lower().replace("_", " ")
    summary = (entry.summary or "").lower()
    return sum(
        _IDENTIFIER_WEIGHT
        if _matches(term, identifiers)
        else _SUMMARY_WEIGHT
        if _matches(term, summary)
        else 0
        for term in terms
    )


@dataclass(frozen=True, slots=True)
class OperationIndex:
    """Every callable operation on one client, searchable by keyword.

    Attributes:
        entries: Operations in selector order, published and unpublished alike.
        source_digest: Digest over the selectors, so a consumer can tell that
            the surface changed without diffing it.
        descriptor_version: Value of :data:`DESCRIPTOR_VERSION` at build time.
        built_at: When the index was built, in UTC.
    """

    entries: tuple[OperationEntry, ...]
    source_digest: str
    descriptor_version: str
    built_at: datetime
    _by_published: dict[str, OperationEntry] = field(repr=False, compare=False)
    _by_selector: dict[str, OperationEntry] = field(repr=False, compare=False)

    def __len__(self) -> int:
        """Return the number of operations, published and unpublished."""
        return len(self.entries)

    def __iter__(self) -> Iterator[OperationEntry]:
        """Iterate every operation in selector order."""
        return iter(self.entries)

    @property
    def published(self) -> tuple[OperationEntry, ...]:
        """Operations offered to agents."""
        return tuple(e for e in self.entries if e.is_published)

    def get(self, identifier: str) -> OperationEntry:
        """Look up one operation by published identifier or by selector.

        Args:
            identifier: Published identifier or dotted selector.

        Returns:
            The matching operation.

        Raises:
            UnpublishedOperationError: If the operation exists but is
                deliberately unpublished. The error names the reason, so a
                caller never has to guess between "withheld" and "unknown".
            KeyError: If no operation has that identifier.
        """
        entry = self._by_published.get(identifier) or self._by_selector.get(identifier)
        if entry is None:
            raise KeyError(identifier)
        if entry.unpublished_reason is not None:
            raise UnpublishedOperationError(identifier, entry.unpublished_reason)
        return entry

    def search(self, query: str, *, limit: int = 20) -> tuple[OperationEntry, ...]:
        """Rank published operations against a plain-language query.

        An operation has to match **every** term to be a result. Matching any
        one term made a phrase search worse the more words it was given:
        "deploy a model" returned 325 of 332 operations, because something in
        the platform mentions "model" almost everywhere. With every term
        required it returns 12, best first. Each extra word now narrows,
        which is what a searcher means by adding one.

        When nothing matches every term the requirement drops by one and the
        search runs again, down to a single term. So a four-word phrase that
        nothing satisfies completely answers with whatever matched three of
        them, rather than either nothing or everything that matched one.

        Equal scores break on the shorter identifier first, then
        alphabetically. Measured: "list models" scored `list_models` and
        `list_guides_models` identically, and an alphabetical tie-break put the
        guides operation first — so the closest match to what the caller asked
        for was second, and an agent taking the first result called the wrong
        operation. A shorter identifier carrying the same terms has fewer words
        the caller did not ask for, which is what "closer" means here.

        Args:
            query: Words to search for. Case and order do not matter.
            limit: Maximum results to return.

        Returns:
            Up to ``limit`` operations, best first. Empty only when no
            operation matches a single term, which is a genuine nothing-found
            rather than a weak result presented as an answer.
        """
        terms = meaningful_terms(query)
        if not terms:
            return ()
        graded = [(_grade(entry, terms), entry) for entry in self.published]
        for required in range(len(terms), 0, -1):
            rows = [
                (-score, len(entry.published_id), entry.published_id, entry)
                for (score, hits), entry in graded
                if hits >= required
            ]
            if rows:
                rows.sort()
                return tuple(entry for *_, entry in rows[:limit])
        return ()

    def coverage(self) -> dict[str, int]:
        """Return counts the docstring and coverage gates assert against.

        Returns:
            Mapping with ``operations``, ``published``, ``unpublished``,
            ``documented`` and ``services`` counts, measured from this index
            rather than recorded anywhere.
        """
        return {
            "operations": len(self.entries),
            "published": len(self.published),
            "unpublished": len(self.entries) - len(self.published),
            "documented": sum(1 for e in self.entries if e.summary),
            "services": len({e.service for e in self.entries}),
        }


def _grade(entry: OperationEntry, terms: list[str]) -> tuple[int, int]:
    """Score one operation and count how many terms it matched.

    Both numbers come from one pass because the ranking needs the score and
    the narrowing needs the count, and scoring an entry once per term to get
    the second was the obvious version of this that did the work twice.

    Args:
        entry: The operation to grade.
        terms: Lowercased search terms.

    Returns:
        The total score and the number of terms that matched at all.
    """
    identifiers = f"{entry.published_id} {entry.selector}".lower().replace("_", " ")
    summary = (entry.summary or "").lower()
    score = 0
    hits = 0
    for term in terms:
        if _matches(term, identifiers):
            score += _IDENTIFIER_WEIGHT
            hits += 1
        elif _matches(term, summary):
            score += _SUMMARY_WEIGHT
            hits += 1
    return score, hits


def _service_names(client: Any) -> list[str]:
    """Return the client's service attribute names, in declaration order.

    Reads the properties declared on the client class, so a service added to
    the client is indexed without touching this module.

    Args:
        client: A client instance.

    Returns:
        Public property names declared on the client's type.
    """
    return [
        name
        for name, value in vars(type(client)).items()
        if isinstance(value, property) and not name.startswith("_")
    ]


def _describe(method: Any) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """Return a method's parameters, required parameters, and return annotation.

    Args:
        method: Unbound function taken from the service class.

    Returns:
        Tuple of all parameter names, the required subset, and the rendered
        return annotation. An unreadable signature yields empty tuples rather
        than raising, because one odd method must not cost the whole index.
    """
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return (), (), None
    params, required = [], []
    for name, param in sig.parameters.items():
        if name == "self" or param.kind in (
            param.VAR_POSITIONAL,
            param.VAR_KEYWORD,
        ):
            continue
        params.append(name)
        if param.default is param.empty:
            required.append(name)
    returns = None
    if sig.return_annotation is not sig.empty:
        returns = (
            sig.return_annotation
            if isinstance(sig.return_annotation, str)
            else getattr(sig.return_annotation, "__name__", None)
            or str(sig.return_annotation)
        )
    return tuple(params), tuple(required), returns


def _entry_for(service_name: str, method_name: str, method: Any) -> OperationEntry:
    """Build one index entry for a client method.

    Args:
        service_name: Service attribute on the client.
        method_name: Method name on that service.
        method: The unbound function, read for its signature and docstring.

    Returns:
        The entry, carrying its withheld reason when it has one.
    """
    op_selector = selector(service_name, method_name)
    params, required, returns = _describe(method)
    reason = UNPUBLISHED.get(op_selector)
    return OperationEntry(
        selector=op_selector,
        published_id=published_id(op_selector),
        service=service_name,
        method=method_name,
        summary=first_sentence(inspect.getdoc(method)),
        parameters=params,
        required_parameters=required,
        returns=returns,
        unpublished_reason=reason.message() if reason else None,
    )


def _methods_of(service: Any) -> list[tuple[str, Any]]:
    """Return a service object's public methods.

    Args:
        service: A service or sub-client instance.

    Returns:
        ``(name, function)`` pairs for public methods, in name order.
    """
    return [
        (name, method)
        for name, method in inspect.getmembers(
            type(service), predicate=inspect.isfunction
        )
        if not name.startswith("_")
    ]


def _is_platform_sub_client(candidate: Any) -> bool:
    """Whether a nested attribute is a sub-client that calls the platform.

    Two conditions, both necessary. It must be ours, so a third-party object
    held as an attribute is not walked. And it must hold a platform client of
    its own, which is what separates ``catalog.secrets`` from a local helper
    like ``models.quant_manager`` that computes over values already in hand.

    Args:
        candidate: The nested attribute value.

    Returns:
        ``True`` when the object's methods are platform operations.
    """
    if not type(candidate).__module__.startswith("kamiwaza"):
        return False
    return hasattr(candidate, "client") or hasattr(candidate, "_client")


def _sub_clients(service: Any) -> list[tuple[str, Any]]:
    """Return a service's nested platform sub-clients.

    Several services expose whole operation families through a nested client
    rather than their own methods — ``catalog.secrets``, ``enclaves.documents``,
    ``gates.packages``. Those operations are as callable as any other, so the
    index must reach them or FR-005's promise is false.

    Args:
        service: The parent service instance.

    Returns:
        ``(attribute_name, sub_client)`` pairs, in name order.
    """
    found: list[tuple[str, Any]] = []
    for name in sorted(dir(service)):
        if name.startswith("_") or name == "client":
            continue
        try:
            candidate = getattr(service, name)
        except Exception:  # noqa: BLE001 - an unreadable attribute is skipped
            continue
        if _is_platform_sub_client(candidate) and _methods_of(candidate):
            found.append((name, candidate))
    return found


def _entries_for(client: Any, service_name: str) -> list[OperationEntry]:
    """Build every index entry for one service on a client, nested ones included.

    Args:
        client: The client instance.
        service_name: Service attribute to read.

    Returns:
        Entries for the service's public methods and for those of any nested
        platform sub-client, the latter with a dotted
        ``service.subclient.method`` selector. Empty when the service cannot be
        constructed — one unavailable service must not cost the whole index.
    """
    try:
        service = getattr(client, service_name)
    except Exception:  # noqa: BLE001 - an unavailable service is skipped, not fatal
        return []
    entries = [
        _entry_for(service_name, method_name, method)
        for method_name, method in _methods_of(service)
    ]
    entries.extend(
        _entry_for(f"{service_name}.{sub_name}", method_name, method)
        for sub_name, sub_client in _sub_clients(service)
        for method_name, method in _methods_of(sub_client)
    )
    return entries


def build_index(client: Any) -> OperationIndex:
    """Build the operation index from a client instance.

    Requires a client rather than constructing one, so the index is built from
    the object graph the caller actually uses and this module needs no base URL,
    no credential, and no network.

    Args:
        client: A ``KamiwazaClient``, or any object exposing services as
            properties. Service properties are read but no operation is called.

    Returns:
        The index, with counts measured from the client it was built against.
    """
    entries = [
        entry
        for service_name in _service_names(client)
        for entry in _entries_for(client, service_name)
    ]
    entries.sort(key=lambda e: e.selector)
    digest = hashlib.sha256(
        "\n".join(e.selector for e in entries).encode("utf-8")
    ).hexdigest()
    return OperationIndex(
        entries=tuple(entries),
        source_digest=digest,
        descriptor_version=DESCRIPTOR_VERSION,
        built_at=datetime.now(timezone.utc),
        _by_published={e.published_id: e for e in entries},
        _by_selector={e.selector: e for e in entries},
    )
