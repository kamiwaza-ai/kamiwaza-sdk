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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .ids import UNPUBLISHED, UnpublishedOperationError, published_id, selector

__all__ = [
    "DESCRIPTOR_VERSION",
    "OperationEntry",
    "OperationIndex",
    "SearchRanking",
    "build_index",
    "first_sentence",
    "score_terms",
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

#: Longer words that carry no platform meaning, dropped for the same reason.
#:
#: The length rule above stops at two characters, and "the" and "for" are three.
#: Measured on the goal set: "make a new workspace for the security team" put
#: ``create_lab`` first, because its summary reads "Create a lab workspace for
#: experimentation" and so matched "for" and "the" — two more terms than
#: ``create_workrooms`` matched, which decided the narrowing level before any
#: score was compared. An English function word matches the platform's own
#: prose nearly everywhere, so counting it narrows nothing and only rewards
#: whichever docstring happens to be wordier.
#:
#: Function words and the question words a request opens with. A word a member
#: could be reaching for an operation with does not belong here, however common
#: it is: "list", "get" and "run" are all frequent and all name operations, and
#: "from" stays searchable because ``deploy_app_from_garden`` uses it.
_NOISE_TERMS = frozenset(
    {
        "about",
        "all",
        "and",
        "any",
        "are",
        "been",
        "for",
        "has",
        "have",
        "how",
        "into",
        "its",
        "just",
        "many",
        "much",
        "not",
        "now",
        "our",
        "over",
        "right",
        "some",
        "than",
        "that",
        "the",
        "them",
        "their",
        "then",
        "this",
        "very",
        "was",
        "were",
        "what",
        "whether",
        "which",
        "with",
        "yet",
        "you",
        "your",
    }
)

#: Words a member uses, mapped onto the words this client's method names use.
#:
#: Neon's account of MCP tool design holds that "a raw REST operation is not
#: automatically a good agent tool" and that what makes one usable includes the
#: names: a caller who says "shut down a deployment" is asking for
#: ``stop_deployment_serving`` whatever verb the platform picked. Without this
#: the search dropped the caller's verb, fell back to the bare noun, and led
#: with ``get_deployment_apps`` — a reader, for a request to act.
#:
#: Three rules keep the table from rotting:
#:
#: * Expansion is additive, so a word that is already platform vocabulary keeps
#:   matching itself. "stop deployment" is untouched by the ``stop`` group,
#:   which only adds forms for the words the platform does not use.
#: * It maps towards the platform's vocabulary, never away: a caller's "halt"
#:   gains "stop", and no operation name is ever read as "halt".
#: * Every target word is carried by published operations, named in the comment
#:   beside its group, so an entry that maps onto nothing published is visible
#:   here rather than only after a search returns nothing.
#:
#: It stays small on purpose. A dozen groups of words members actually used is
#: a table a reader can audit; a synonym list that tries to cover a language is
#: not, and the answer there is a different instrument, not a longer table.
_CALLER_VOCABULARY: dict[str, tuple[str, ...]] = {
    # stop: stop_deployment_serving, stop_deployment_apps.
    # "down" as in "shut down"; it keeps matching download_* on its own.
    "down": ("stop",),
    "halt": ("stop",),
    "kill": ("stop",),
    "off": ("stop",),
    "shut": ("stop",),
    "shutdown": ("stop",),
    "terminate": ("stop",),
    # delete, remove, clear: delete_subjects, clear_gate_datasets,
    # remove_publisher_catalog_datasets.
    "away": ("delete", "remove", "clear"),
    "revoke": ("delete", "remove", "clear"),
    # create: create_workrooms, create_local_user_auth, create_datasets.
    # "add" is platform vocabulary too (add_publisher_catalog), and keeps it:
    # expansion adds "create" beside it rather than replacing it.
    "add": ("create",),
    "make": ("create",),
    "new": ("create",),
    # workroom: create_workrooms, get_workrooms, export_bundle_workrooms.
    # "workroom" is a prefix of the plural the client uses, so one form covers
    # both.
    "workspace": ("workroom",),
    # gate: clear_gate_datasets, get_gate_datasets, set_gate_datasets. The
    # platform calls a grant on an object a gate; "access" stays searchable on
    # its own, which is what finds check_access_authz and grant_subject_access.
    "access": ("gate",),
    # garden: import_garden_apps, list_garden_apps.
    "marketplace": ("garden",),
    # hardware, nodes: get_hardware_cluster, list_nodes_cluster,
    # get_running_nodes_cluster. Members ask about GPUs; no operation is named
    # for one, because the platform reports them per node and per host.
    "gpu": ("hardware", "nodes"),
    "gpus": ("hardware", "nodes"),
}


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
        Lowercased terms, with the noise-length ones and the function words in
        :data:`_NOISE_TERMS` dropped when real terms remain. Empty for an
        empty query, which the caller reports as nothing found rather than
        matching everything. A query of nothing but noise keeps its words, so
        "id" and "the" still search for something instead of silently
        matching every operation.
    """
    terms = [t for t in query.lower().split() if t]
    signal = [
        t
        for t in terms
        if len(t) >= _SHORTEST_MEANINGFUL_TERM and t not in _NOISE_TERMS
    ]
    return signal or terms


@dataclass(frozen=True, slots=True)
class SearchRanking:
    """One search's results together with how they were obtained.

    A surface that only sees the operations cannot tell a precise hit from a
    fallback: "stop deployment" and "halt deployment" both answer with
    deployment operations, but the second one answers on "deployment" alone
    because no operation is named "halt". ``required_terms`` is what lets the
    surface say which happened.

    Attributes:
        entries: Matching operations, best first, already cut to the limit.
        terms: The terms actually searched, noise-length ones already dropped.
            Empty when the query carried no terms.
        required_terms: How many of ``terms`` every returned operation
            matched. Equal to ``len(terms)`` for a precise hit, lower when the
            search relaxed, 0 when nothing matched at all.
        matched_count: How many operations matched at that level, before the
            limit cut the list. Tells a caller there is more behind the limit.
    """

    entries: tuple[OperationEntry, ...]
    terms: tuple[str, ...]
    required_terms: int
    matched_count: int

    @property
    def relaxed(self) -> bool:
        """Whether fewer terms were required than the caller supplied."""
        return bool(self.terms) and self.required_terms < len(self.terms)


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

        Thin wrapper over :meth:`search_ranking`, kept because a caller that
        only wants the operations should not have to unpack a report.

        Args:
            query: Words to search for. Case and order do not matter.
            limit: Maximum results to return.

        Returns:
            Up to ``limit`` operations, best first. Empty only when no
            operation matches a single term, which is a genuine nothing-found
            rather than a weak result presented as an answer.
        """
        return self.search_ranking(query, limit=limit).entries

    def search_ranking(self, query: str, *, limit: int = 20) -> SearchRanking:
        """Rank published operations and report how the ranking was reached.

        An operation has to match **every** term to be a result. Matching any
        one term made a phrase search worse the more words it was given:
        "deploy a model" returned 325 of 332 operations, because something in
        the platform mentions "model" almost everywhere. With every term
        required it returns 12, best first. Each extra word now narrows,
        which is what a searcher means by adding one.

        When nothing matches every term the requirement drops by one and the
        search runs again, down to a single term. So a four-word phrase that
        nothing satisfies completely answers with whatever matched three of
        them, rather than either nothing or everything that matched one. The
        returned ``required_terms`` says which level answered, because a
        one-term fallback reads exactly like a precise hit otherwise: "halt
        deployment" has no operation named "halt", so it answers on
        "deployment" alone and a surface needs to be able to say so.

        Equal scores break on the leading term's position first: an operation
        whose identifier *begins* with the caller's first word outranks one
        that carries it later, and both outrank one that matched only in a
        summary. Measured: "stop deployment" and "delete user" both put a
        ``get_*`` operation first without it, because the next tie-break is
        the shorter identifier and a generic reader is always shorter than the
        verb the caller asked for. Length still breaks what position cannot:
        "list models" scored ``list_models`` and ``list_guides_models``
        identically, and a shorter identifier carrying the same terms has
        fewer words the caller did not ask for. Alphabetical order settles the
        rest, so the same words always return the same sequence.

        Args:
            query: Words to search for. Case and order do not matter.
            limit: Maximum results to return.

        Returns:
            A :class:`SearchRanking` holding up to ``limit`` operations, best
            first, the terms searched, how many of them every returned
            operation matched, and how many operations matched at that level.
        """
        terms = meaningful_terms(query)
        if not terms:
            return SearchRanking((), (), 0, 0)
        graded = [
            (score_terms(terms, (entry.published_id, entry.selector), entry.summary), entry)
            for entry in self.published
        ]
        for required in range(len(terms), 0, -1):
            rows = [
                (-score, -lead, len(entry.published_id), entry.published_id, entry)
                for (score, hits, lead), entry in graded
                if hits >= required
            ]
            if rows:
                rows.sort()
                return SearchRanking(
                    entries=tuple(entry for *_, entry in rows[:limit]),
                    terms=tuple(terms),
                    required_terms=required,
                    matched_count=len(rows),
                )
        return SearchRanking((), tuple(terms), 0, 0)

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


def score_terms(
    terms: Sequence[str],
    identifiers: Sequence[str],
    summary: str | None,
) -> tuple[int, int, int]:
    """Grade one searchable thing against a query's terms.

    Public because the workflow tools rank their own specs by name and
    summary, and a second scorer beside this one would drift from it. All
    three numbers come from one pass because the ranking needs the score and
    the lead position while the narrowing needs the count.

    Each term is matched as itself first and, when the platform uses a
    different word for the same act, also as that word (see
    :data:`_CALLER_VOCABULARY`). Expansion happens here rather than where the
    query is split, so the workflow search gets it from the same call and a
    term the caller gave stays one term: a match on an expanded form counts as
    a hit of the caller's word, which is what keeps the relaxation count
    honest. "shut down a deployment" therefore matches all three of its terms
    against ``stop_deployment_serving`` and is reported as unrelaxed, while
    "send an invoice to a customer" still matches one of its three and is
    reported as the weak fallback it is.

    Args:
        terms: Lowercased search terms, the caller's leading one first. An
            empty sequence grades everything as zero, since a query with no
            terms is reported as nothing found rather than matching all.
        identifiers: Names to match, the primary one first. Underscores and
            dots read as word breaks.
        summary: One sentence of prose to match, or ``None``.

    Returns:
        The total score, the number of terms that matched at all, and where
        the leading term landed: 2 when the primary identifier begins with it,
        1 when an identifier carries it further along, 0 when only the summary
        did or nothing did.
    """
    words = [name.lower().replace("_", " ") for name in identifiers]
    haystack = " ".join(words)
    prose = (summary or "").lower()
    score = 0
    hits = 0
    for term in terms:
        forms = _CALLER_VOCABULARY.get(term)
        if _matches_any(term, forms, haystack):
            score += _IDENTIFIER_WEIGHT
            hits += 1
        elif _matches_any(term, forms, prose):
            score += _SUMMARY_WEIGHT
            hits += 1
    lead = _lead_position(words, terms[0]) if terms else 0
    return score, hits, lead


def _matches_any(term: str, forms: tuple[str, ...] | None, text: str) -> bool:
    """Whether a term or one of its platform forms starts a word in text.

    Args:
        term: An already-lowercased search term.
        forms: The term's platform forms, or ``None`` when it has none. Most
            terms have none, so nothing is built for them.
        text: Already-lowercased text to search.

    Returns:
        Whether the term itself, or any of its forms, matches.
    """
    if _matches(term, text):
        return True
    return forms is not None and any(_matches(form, text) for form in forms)


def _lead_position(words: Sequence[str], lead: str) -> int:
    """Where the caller's first term sits in a thing's identifiers.

    Positional rather than a list of known verbs: a caller who says "stop
    deployment" names the action first, so an identifier that opens with that
    word is the closest thing to what was asked for. The leading term carries
    its platform forms with it, because "shut down a deployment" names the
    action just as plainly and ``stop_deployment_serving`` opens with it.

    Args:
        words: Identifiers with underscores already read as word breaks, the
            primary one first.
        lead: The query's first meaningful term, already lowercased.

    Returns:
        2 when the primary identifier begins with the term or one of its
        platform forms, 1 when the identifiers carry one somewhere later, 0
        otherwise.
    """
    forms = (lead, *_CALLER_VOCABULARY.get(lead, ()))
    if words and words[0].startswith(forms):
        return 2
    haystack = " ".join(words)
    return 1 if any(_matches(form, haystack) for form in forms) else 0


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
