"""Behaviour hints, approval, and a description for each operation.

Two facts about an operation decide how a host may present and gate it, per the
MCP server's FR-012a: whether it only reads, and whether it needs approval
before it runs. Both are stated here, in code the server ships, so neither can
be changed by a caller or a request.

Alongside them, two derivations:

* **behaviour hints** — read-only, destructive, idempotent, open-world — each
  derived from the method name's leading verb, because this client names
  operations by what they do;
* a **description** — resolved by precedence from the method docstring, then
  the interface document, and never authored in the consuming server.

Derivation is the default and :data:`HINT_OVERRIDES` corrects one operation at
a time, so a single wrong answer does not cost derivation for the other 331.
There is deliberately no taxonomy of operation kinds here: each hint is read
straight off the verb, and the published contract is the two facts above.
A verb no set knows is treated as a mutation needing approval — the safe
reading of "we do not know what this does" — and :func:`unknown_verbs` reports
it so the gap gets closed rather than discovered in production.

Protocol-neutral by construction: a hint is a plain boolean on a dataclass here,
and the MCP server maps it onto the wire shape its revision requires.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, replace
from enum import Enum
from functools import lru_cache
from importlib import resources
from typing import Any

from .spec_index import OperationEntry, OperationIndex, first_sentence

__all__ = [
    "APPROVAL_REQUIRED_READS",
    "HINT_OVERRIDES",
    "OPEN_WORLD_OPERATIONS",
    "OperationDescriptor",
    "BehaviourHints",
    "DescriptionSource",
    "derive_hints",
    "describe",
    "describe_all",
    "description_coverage",
    "interface_descriptions",
    "resolve_description",
    "resolve_service",
    "unknown_verbs",
]



#: Leading verbs that read state without changing it.
_READ_VERBS = frozenset(
    {
        "get",
        "list",
        "search",
        "find",
        "check",
        "health",
        "metadata",
        "query",
        "retrieve",
        "estimate",
        "diagnose",
        "capabilities",
        "operations",
        "grants",
        "filter",
        "evaluate",
        "by",
        "stream",
        # `flight`, `ontology`, `agentic` and `slack` name a subject, not an
        # action, so they stay only because every operation that leads with
        # them reads: `retrieval.flight_batches`, `context.ontology_health`,
        # `context.agentic_search`, `retrieval.slack_messages`. A new operation
        # leading with one of these must be checked by hand rather than
        # inheriting a read.
        "flight",
        "ontology",
        "agentic",
        "slack",
        # The connector-surface reads that arrived with the surface browsing
        # API. Each one reads and returns: `browse_surface` lists a surface's
        # items and `fetch_surface_content` reads one node by its opaque id.
        #
        # `verify` and `discover` were here once and are not verbs any more.
        # Each led exactly one published operation, and in both cases that one
        # operation writes: `connectors.verify_connection` persists connection
        # health and `gates.discover` imports a caller-supplied classpath
        # server-side. Both are stated in `HINT_OVERRIDES` instead, so no new
        # operation can inherit a read from either word.
        "browse",
        "fetch",
    }
)

#: Leading verbs that bring something into existence or add a member.
#:
#: These are the verbs a repeat call is *not* safe for: calling one twice makes
#: two things, so they are the only verbs that clear the idempotent hint.
_ADD_VERBS = frozenset(
    {
        "create",
        "add",
        "deploy",
        "register",
        "install",
        "trigger",
        "import",
        "insert",
        "upload",
        "submit",
        "run",
        "rerun",
        "retry",
        "start",
        "schedule",
        "initiate",
        "pair",
        "bind",
        "subscribe",
        "download",
        "pull",
        "load",
        "enter",
        "send",
        "emit",
        "store",
        "materialize",
        "export",
        "fix",
        "login",
        "complete",
    }
)

#: Leading verbs that modify something that already exists.
_CHANGE_VERBS = frozenset(
    {
        "update",
        "patch",
        "set",
        "upsert",
        "replace",
        "toggle",
        "change",
        "reset",
        "rotate",
        "refresh",
        "scale",
        "stop",
        "cancel",
        "wait",
        "forward",
        "reconnect",
        "archive",
        "deprecate",
        "unload",
        "logout",
        "leave",
        "clear",
    }
)

#: Leading verbs that remove something, or revoke access to it.
#:
#: These set the destructive hint. Everything outside this set and outside
#: :data:`_READ_VERBS` still changes state; it just does not end anything.
_REMOVE_VERBS = frozenset(
    {
        "delete",
        "remove",
        "purge",
        "revoke",
        "uninstall",
        "withdraw",
    }
)

#: Operations that reach a system outside this platform's control.
#:
#: A curated allowlist, defaulting to false, because an agent treats an
#: open-world call as one whose result it cannot predict from platform state.
#: Model hubs and garden registries are external; everything else is not.
OPEN_WORLD_OPERATIONS: frozenset[str] = frozenset(
    {
        # Model hub reads and downloads reach Hugging Face, not this platform.
        "models.search_hub_model_files",
        "models.initiate_model_download",
        "models.download_and_deploy_model",
        "models.get_model_download_status",
        "models.get_model_files_download_status",
        "models.check_download_status",
        "models.wait_for_download",
        # Container image status and pulls reach an external registry.
        "apps.check_image_status",
        "apps.pull_images",
        # Garden discovery and import reach the external garden registry. The
        # `tools.*` three are withheld with the rest of that deprecated
        # service; the `apps.*` three are published and reach the same
        # registry, so the flag has to be on both or a host sees the published
        # path as closed-world.
        "tools.discover_servers",
        "tools.import_garden_servers",
        "tools.get_garden_status",
        "apps.list_garden_apps",
        "apps.import_garden_apps",
        # `install_by_name` imports the garden catalog itself when the named
        # template is missing locally and `sync_if_missing` is left on.
        "apps.install_by_name",
        # Probes the third-party provider live, under the member's stored
        # credential, so the result depends on a system this platform does
        # not own.
        "connectors.verify_connection",
    }
)

#: Reads that require approval anyway, per FR-006g and FR-020.
#:
APPROVAL_REQUIRED_READS: frozenset[str] = frozenset(
    {
        # Enumerates every secret held by the platform. The values are absent,
        # but the inventory itself is disclosure. Both selectors list the same
        # secrets — the facade and the nested client — so naming only one left
        # the other a free call for the identical disclosure.
        "catalog.list_secrets",
        "catalog.secrets.list",
        # Enumerates members and their identity records.
        "auth.list_users",
        "auth.get_user",
        # Enumerates active personal access tokens.
        "auth.list_pats",
        # Reads one secret's metadata by URN. The value is absent here too,
        # but the rationale above is about disclosure rather than volume: an
        # agent that can name a secret learns it exists, who holds it, and
        # when it was last rotated. Leaving the single-record read free made
        # the listing gate avoidable by anyone who could guess a URN.
        "catalog.secrets.get",
        # Returns a presigned download URL for an original document. The URL
        # carries its own authority and works outside this call, so the
        # result is closer to a handed-out credential than to a read.
        "context.get_document_download_url",
    }
)


@dataclass(frozen=True, slots=True)
class BehaviourHints:
    """Advisory hints a host may use to decide how to present an operation.

    Advisory is the operative word: a host that ignores every hint must still be
    refused server-side. These exist so a host that has never seen this surface
    can tell a read from a mutation before calling it, without reading prose.

    Attributes:
        read_only: The operation does not change platform state.
        destructive: The operation removes something or ends its life.
        idempotent: Repeating the call with the same arguments has the same
            effect as calling it once.
        open_world: The operation reaches a system outside this platform.
    """

    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool


#: Per-operation hint overrides, applied after derivation and taking precedence.
#:
#: Follows `@neon/tools`'s `spec.annotations ?? generated.annotations`: a wrong
#: derivation for one operation is corrected here rather than by weakening the
#: rule for everything.
#:
#: An entry replaces every derived hint except `open_world`, which
#: :func:`describe` re-applies from :data:`OPEN_WORLD_OPERATIONS` afterwards.
#: Without that an override written for the other three hints would quietly
#: close the open-world flag on an allowlisted operation.
HINT_OVERRIDES: dict[str, BehaviourHints] = {
    # `verify` is not a read verb here. The platform probes the provider and
    # persists the resulting connection health, so one call can move a
    # member's stored connection into degraded or reauth-required. The probe
    # reaches the third-party provider, so it is open-world as well.
    #
    # Not idempotent, and the docstring is why: it says the call issues live
    # third-party calls and must not be polled on a timer or fanned out across
    # a catalog. `envelopes.platform_fault` publishes `idempotent` as
    # `safe_to_retry`, so declaring it here would tell a host that the one
    # thing the docstring forbids is safe. Repeating the call also re-probes
    # the provider and can land on a different health than the first, which is
    # not the same-result-on-repeat this hint promises.
    "connectors.verify_connection": BehaviourHints(
        read_only=False, destructive=False, idempotent=False, open_world=True
    ),
    # `POST /authz/gates/discover` imports a caller-supplied dotted classpath
    # server-side, which runs that module's top-level code inside the
    # authorization surface. Nothing is stored, but an import is an execution,
    # so it cannot publish as a free read. Re-importing the same classpath
    # reaches the same loaded module, so it stays idempotent.
    "gates.discover": BehaviourHints(
        read_only=False, destructive=False, idempotent=True, open_world=False
    ),
    # Returns the new key once and only once, so a retry is not equivalent to
    # the first call: the caller loses the key it did not read.
    "cluster.rotate_preshared_key": BehaviourHints(
        read_only=False, destructive=True, idempotent=False, open_world=False
    ),
    # Replacing an installed gate package re-resolves every existing binding,
    # so a repeat is not a no-op and a failed one can leave bindings pointing
    # at the previous package. FR-014c requires this stated, not derived.
    "gates.packages.replace": BehaviourHints(
        read_only=False, destructive=True, idempotent=False, open_world=False
    ),
    # "stop" reads as an ordinary state change, but nothing is recoverable
    # afterwards, so the destructive hint and approval must both fire.
    "serving.stop_deployment": BehaviourHints(
        read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "apps.stop_deployment": BehaviourHints(
        read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    # Retires the outgoing pre-shared key: the window closes and the old key is
    # gone, which destroys something rather than changing it.
    "cluster.complete_key_rotation": BehaviourHints(
        read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    # Removes every session. "purge" already derives as destructive; named here
    # because the blast radius is every signed-in member rather than one object.
    "auth.purge_sessions": BehaviourHints(
        read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    # Mints a new token rather than reading an existing one, so a repeat is not
    # a no-op. Withheld from the published set as well, but the hints must be
    # right regardless of whether anything currently reads them.
    "auth.refresh_access_token": BehaviourHints(
        read_only=False, destructive=False, idempotent=False, open_world=False
    ),
    # `admin`, `declare`, `chat` and `call` are subjects, moods and transports
    # rather than actions, so no verb set holds them and every operation that
    # leads with one is stated here instead. A new one fails closed as an
    # unknown verb and `unknown_verbs()` reports it.
    #
    # `admin_delete` is `DELETE /admin/workrooms/{id}`; `admin_list` reads.
    "workrooms.admin_delete": BehaviourHints(
        read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "workrooms.admin_list": BehaviourHints(
        read_only=True, destructive=False, idempotent=True, open_world=False
    ),
    # Registers realm vocabulary with `PUT /cluster/attribute-schema/{name}`:
    # a write, and re-declaring the same schema is a no-op, so idempotent but
    # never read-only.
    "cluster.declare_attribute": BehaviourHints(
        read_only=False, destructive=False, idempotent=True, open_world=False
    ),
    # `chat` posts a message into a member's conversation with
    # `POST /conversations/{id}/messages` and then triggers an agent run whose
    # own tool use is unbounded, so it is the last operation that should skip
    # the approval gate. Each call appends a turn, so a repeat is not a no-op.
    "conversations.chat": BehaviourHints(
        read_only=False, destructive=False, idempotent=False, open_world=False
    ),
    # Same write on canonical Kaizen: submits an input and runs the agent. The
    # optional idempotency key is the caller's to supply, so the operation
    # itself is not idempotent.
    "conversations.chat_canonical": BehaviourHints(
        read_only=False, destructive=False, idempotent=False, open_world=False
    ),
    # Raises `DeprecationWarning` on every call and reaches no platform route
    # at all, so it changes nothing and a repeat raises the same way. Withheld
    # for that raise, and the hints are stated regardless of who reads them.
    "embedding.call": BehaviourHints(
        read_only=True, destructive=False, idempotent=True, open_world=False
    ),
}


@dataclass(frozen=True, slots=True)
class OperationDescriptor:
    """One operation with everything a host needs to present and gate it.

    The two facts FR-012a requires are ``hints.read_only`` and
    ``requires_approval``. They live here rather than beside each other in a
    taxonomy so there is exactly one place either can be read from.

    Attributes:
        entry: The indexed operation this describes.
        hints: Advisory behaviour hints, including whether it only reads.
        requires_approval: Whether a member must approve before the call runs.
        returns_credential: Whether the result carries a credential. Such an
            operation is withheld rather than published, so this records why.
        paginated: Whether the operation takes paging arguments.
    """

    entry: OperationEntry
    hints: BehaviourHints
    requires_approval: bool
    returns_credential: bool
    paginated: bool

    @property
    def published_id(self) -> str:
        """Identifier an agent host sees."""
        return self.entry.published_id

    @property
    def selector(self) -> str:
        """Internal dotted identifier."""
        return self.entry.selector


_PAGING_PARAMETERS = frozenset({"page", "per_page", "limit", "offset", "cursor"})


def derive_hints(op_selector: str, method: str) -> BehaviourHints | None:
    """Derive an operation's hints from its leading verb.

    Each hint is read straight off verb-set membership rather than through an
    intermediate category:

    * read-only when the verb reads;
    * destructive when the verb removes or revokes something;
    * idempotent unless the verb adds something, because calling an add twice
      makes two things while a second change or removal finds the work done;
    * open-world when the selector is on the external allowlist.

    Args:
        op_selector: Dotted ``service.method`` selector, checked against the
            open-world allowlist.
        method: Method name, whose leading token carries the verb.

    Returns:
        The derived hints, or ``None`` when the leading verb is in no verb set.
        A ``None`` is a gap to close in :data:`HINT_OVERRIDES`, never a silent
        default to read-only.
    """
    verb = method.split("_")[0].lower()
    known = _READ_VERBS | _ADD_VERBS | _CHANGE_VERBS | _REMOVE_VERBS
    if verb not in known:
        return None
    return BehaviourHints(
        read_only=verb in _READ_VERBS,
        destructive=verb in _REMOVE_VERBS,
        idempotent=verb not in _ADD_VERBS,
        open_world=op_selector in OPEN_WORLD_OPERATIONS,
    )


#: Hints for an operation whose verb no set knows.
#:
#: Not read-only, not idempotent, and destructive, so every gate an unknown
#: operation could need fires. The operation still ships — FR-005c requires it
#: reachable the day it appears — and :func:`unknown_verbs` reports it so the
#: real answer gets recorded. Refusing to describe it would take the whole
#: catalog down over one new verb.
_UNKNOWN_VERB_HINTS = BehaviourHints(
    read_only=False, destructive=True, idempotent=False, open_world=False
)


def describe(entry: OperationEntry) -> OperationDescriptor:
    """Build the descriptor for one indexed operation.

    Args:
        entry: An operation from the index.

    Returns:
        The descriptor. An override replaces the derived hints wholesale except
        for ``open_world``, which is re-applied from
        :data:`OPEN_WORLD_OPERATIONS` so an override cannot close it by
        omission. An unknown verb falls back to :data:`_UNKNOWN_VERB_HINTS`,
        which gates everything.
    """
    hints = (
        HINT_OVERRIDES.get(entry.selector)
        or derive_hints(entry.selector, entry.method)
        or _UNKNOWN_VERB_HINTS
    )
    if entry.selector in OPEN_WORLD_OPERATIONS and not hints.open_world:
        hints = replace(hints, open_world=True)
    requires_approval = not hints.read_only or entry.selector in APPROVAL_REQUIRED_READS
    return OperationDescriptor(
        entry=entry,
        hints=hints,
        requires_approval=requires_approval,
        returns_credential=not entry.is_published
        and "credential" in (entry.unpublished_reason or "").lower(),
        paginated=bool(_PAGING_PARAMETERS & set(entry.parameters)),
    )


def describe_all(index: OperationIndex) -> tuple[OperationDescriptor, ...]:
    """Describe every published operation in an index.

    Unpublished operations are skipped: they are never presented to a host.

    Args:
        index: The operation index.

    Returns:
        Descriptors in index order. An operation whose verb is unknown is
        described conservatively rather than omitted, so the surface never
        shrinks silently; call :func:`unknown_verbs` to find them.
    """
    return tuple(describe(entry) for entry in index.published)


def unknown_verbs(index: OperationIndex) -> tuple[str, ...]:
    """Return the selectors whose leading verb no set knows.

    The docstring and coverage gates call this rather than discovering the gap
    by crashing at describe time.

    Args:
        index: The operation index.

    Returns:
        Selectors of published operations with an unknown verb and no override,
        in index order. Empty when every operation derives or is corrected.
    """
    return tuple(
        entry.selector
        for entry in index.published
        if entry.selector not in HINT_OVERRIDES
        and derive_hints(entry.selector, entry.method) is None
    )


class DescriptionSource(str, Enum):
    """Where a resolved description came from.

    Recorded so the documentation gate can report *which* source answered, and
    so a thin description traced to the interface document is distinguishable
    from one traced to a docstring that needs writing.
    """

    DOCSTRING = "docstring"
    INTERFACE_DESCRIPTION = "interface_description"
    INTERFACE_SUMMARY = "interface_summary"
    ABSENT = "absent"


#: File name of the generated description table, shipped as package data so a
#: consumer that installed the wheel can read it.
#:
#: Generated from the vendored platform document by
#: ``scripts/regenerate_interface_descriptions.py``, and deliberately *not*
#: that document itself. The document is a development artifact — the drift
#: gate's input — and only its ``summary`` and ``description`` fields are read
#: at runtime: 41 KiB of 278 KiB when this split was made. Shipping the whole
#: thing put the rest of it in every install and made a vendored snapshot of
#: the platform's document look like part of this package's API surface, which
#: a consumer could read and mistake for the platform's current truth.
_INTERFACE_DOCUMENT = "INTERFACE_DESCRIPTIONS.json"

_REQUEST_CALL = re.compile(
    r'_request\(\s*["\'](GET|POST|PUT|PATCH|DELETE)["\']\s*,\s*f?["\']([^"\']*)["\']'
)
_VERB_CALL = re.compile(
    r'self\.client\.(get|post|put|patch|delete)\(\s*f?["\']([^"\']*)["\']'
)
_PATH_PARAMETER = re.compile(r"\{[^}]*\}")


def _normalise_path(path: str) -> str:
    """Return a path with every parameter segment reduced to a single token.

    The client interpolates its own names (``{model_id}``) while the interface
    document uses the route's (``{model_uuid}``), so the names cannot be
    compared. The *shape* can.

    Args:
        path: Request path, possibly containing parameter placeholders.

    Returns:
        The path with each placeholder replaced by ``{}`` and any trailing
        slash removed, so ``/models/`` and ``/models`` compare equal.
    """
    flattened = _PATH_PARAMETER.sub("{}", path)
    return flattened.rstrip("/") or "/"


def _read_interface_document() -> str | None:
    """Read the generated description table, or ``None`` when it is not there.

    Returns:
        The table's text, or ``None`` when the package data is missing — which
        degrades description resolution to docstrings rather than failing the
        whole surface.
    """
    try:
        return (
            resources.files("kamiwaza_sdk.agent_tools")
            .joinpath(_INTERFACE_DOCUMENT)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None


@lru_cache(maxsize=1)
def interface_descriptions() -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """Return the platform's own wording, keyed by method and path.

    Reads the generated table rather than the vendored document, so the paths
    arrive already normalised and no process pays to walk 278 KiB of JSON for
    the 233 pairs of strings it wants. Cached because the package data cannot
    change inside a process.

    Returns:
        Mapping from ``(http_method, normalised_path)`` to the operation's
        ``(description, summary)``, either of which is ``None`` when the
        platform states only the other. Empty when the table is not installed,
        which degrades description resolution to docstrings rather than failing
        the surface.
    """
    raw = _read_interface_document()
    if raw is None:
        return {}
    table: dict[str, dict[str, str]] = json.loads(raw)
    resolved: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for signature, stated in table.items():
        http_method, _, path = signature.partition(" ")
        if not path:
            continue
        resolved[(http_method.upper(), path)] = (
            stated.get("description"),
            stated.get("summary"),
        )
    return resolved


def _request_signature(service: Any, method_name: str) -> tuple[str, str] | None:
    """Return the HTTP method and path a client method calls, if it is literal.

    Read from the method's source rather than by calling it, because resolving
    a description must never issue a request. A method that builds its path
    dynamically yields ``None``: 47 of 310 do at this revision, and they fall
    back to their docstring, which is the source this precedence prefers anyway.

    Args:
        service: The service object the method belongs to.
        method_name: Name of the method.

    Returns:
        ``(http_method, normalised_path)``, or ``None`` when no literal request
        call is present in the source.
    """
    function = getattr(type(service), method_name, None)
    if function is None:
        return None
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        return None
    match = _REQUEST_CALL.search(source) or _VERB_CALL.search(source)
    if match is None:
        return None
    return match.group(1).upper(), _normalise_path(match.group(2))


def resolve_service(client: Any, service_path: str) -> Any | None:
    """Return the service object a service path names on a client.

    A path is dotted when the operation lives on a nested platform sub-client —
    ``catalog.secrets``, ``enclaves.documents``, ``gates.packages`` — which
    :mod:`.spec_index` records as ``parent.child``. One ``getattr`` with that
    whole name never matches an attribute, so each segment is walked in turn.
    Without the walk a nested operation reaches no service object and can only
    resolve from its docstring, which puts the interface-document rungs of the
    FR-006h precedence out of reach for it.

    Args:
        client: The client the index was built from.
        service_path: Service attribute name, dotted for a nested sub-client.

    Returns:
        The resolved service object, or ``None`` when any segment is missing.
    """
    resolved: Any = client
    for segment in service_path.split("."):
        resolved = getattr(resolved, segment, None)
        if resolved is None:
            return None
    return resolved


def resolve_description(
    entry: OperationEntry,
    service: Any = None,
) -> tuple[str | None, DescriptionSource]:
    """Resolve one operation's description, and say where it came from.

    Precedence, per FR-006h: the method docstring's first sentence, then the
    interface document's description first sentence, then its summary. This
    **inverts** the reference implementation's order deliberately — their
    descriptions come from OpenAPI because their docstrings are not the source;
    ours are, which is why the docstring gate exists.

    No text is authored here and none in the consuming server: a description
    ships with the method it describes, so the person changing behaviour is the
    person who updates the sentence an agent ranks on.

    Args:
        entry: The indexed operation.
        service: The service object, needed to read the method's source and map
            it to an interface-document operation. Omit it to resolve from the
            docstring alone.

    Returns:
        The description and its source. ``(None, ABSENT)`` when no source has
        one, which is what the gate fails on.
    """
    if entry.summary:
        return entry.summary, DescriptionSource.DOCSTRING
    if service is None:
        return None, DescriptionSource.ABSENT
    signature = _request_signature(service, entry.method)
    if signature is None:
        return None, DescriptionSource.ABSENT
    described = interface_descriptions().get(signature)
    if described is None:
        return None, DescriptionSource.ABSENT
    description, summary = described
    sentence = first_sentence(description)
    if sentence:
        return sentence, DescriptionSource.INTERFACE_DESCRIPTION
    if summary and summary.strip():
        return summary.strip(), DescriptionSource.INTERFACE_SUMMARY
    return None, DescriptionSource.ABSENT


def description_coverage(index: OperationIndex, client: Any) -> dict[str, int]:
    """Count where published operations get their descriptions from.

    The numbers the documentation gate and the PRD both quote, measured rather
    than recorded.

    Args:
        index: The operation index.
        client: The client the index was built from, used to reach each service.

    Returns:
        Mapping from each :class:`DescriptionSource` value to its count, plus
        ``thin`` for resolved descriptions under six words — present but too
        short to choose between operations on.
    """
    counts = {source.value: 0 for source in DescriptionSource}
    counts["thin"] = 0
    for entry in index.published:
        service = resolve_service(client, entry.service)
        description, source = resolve_description(entry, service)
        counts[source.value] += 1
        if description and len(description.split()) < 6:
            counts["thin"] += 1
    return counts
