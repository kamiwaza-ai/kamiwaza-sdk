"""Effect classification, behaviour hints, and approval for each operation.

Three derivations, each with an override, per the MCP server's FR-006a to
FR-006k:

* an **effect** — read, create, update, or destroy — derived from the method
  name's leading verb, because this client names operations by what they do;
* **behaviour hints** — read-only, destructive, idempotent, open-world — derived
  from that effect rather than from an HTTP verb;
* an **approval requirement** — every non-read, plus the reads named in
  :data:`APPROVAL_REQUIRED_READS`.

Derivation is the default and an override is per operation, so one wrong answer
is corrected without abandoning derivation for the other 309. A derivation that
cannot classify a verb says so rather than guessing: :data:`EFFECT_OVERRIDES`
is where the answer goes, and :func:`unclassified` lists what is still missing.

Protocol-neutral by construction: a hint is a plain boolean on a dataclass here,
and the MCP server maps it onto the wire shape its revision requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .spec_index import OperationEntry, OperationIndex

__all__ = [
    "APPROVAL_REQUIRED_READS",
    "EFFECT_OVERRIDES",
    "Effect",
    "HINT_OVERRIDES",
    "OPEN_WORLD_OPERATIONS",
    "OperationDescriptor",
    "BehaviourHints",
    "classify",
    "describe",
    "describe_all",
    "unclassified",
]


class Effect(str, Enum):
    """What an operation does to platform state.

    Four values rather than a read/write boolean, because approval and the
    destructive hint need to tell "replaces a field" from "removes the object".
    """

    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DESTROY = "destroy"

    @property
    def is_read(self) -> bool:
        """Whether the effect leaves platform state unchanged."""
        return self is Effect.READ


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
        "discover",
        "estimate",
        "diagnose",
        "capabilities",
        "operations",
        "grants",
        "filter",
        "evaluate",
        "require",
        "by",
        "stream",
        "chat",
        "call",
        "encode",
        "flight",
        "ontology",
        "agentic",
        "slack",
        "admin",
        "auto",
        "declare",
    }
)

#: Leading verbs that bring something into existence or add a member.
_CREATE_VERBS = frozenset(
    {
        "create",
        "add",
        "deploy",
        "register",
        "install",
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
_UPDATE_VERBS = frozenset(
    {
        "update",
        "patch",
        "set",
        "upsert",
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
_DESTROY_VERBS = frozenset(
    {
        "delete",
        "remove",
        "purge",
        "revoke",
        "withdraw",
    }
)

#: Operations whose effect the verb rule gets wrong, with the correct effect.
#:
#: Each entry is a correction with a reason, not a preference. An empty mapping
#: would mean the verb rule is right for all 310 operations, which is a claim
#: this file should have to earn one entry at a time.
EFFECT_OVERRIDES: dict[str, Effect] = {
    # Ends a deployment's life. "stop" reads as a state change, but nothing is
    # recoverable afterwards, so approval and the destructive hint must fire.
    "serving.stop_deployment": Effect.DESTROY,
    "apps.stop_deployment": Effect.DESTROY,
    # Retires the outgoing pre-shared key. The window closes and the old key is
    # gone, which is destruction rather than an update.
    "cluster.complete_key_rotation": Effect.DESTROY,
    # Removes every session. "purge" is already destructive; named here because
    # the blast radius is every signed-in member rather than one object.
    "auth.purge_sessions": Effect.DESTROY,
    # Creates a token exchange rather than reading one. Withheld from the
    # published set too, but the classification must be right regardless.
    "auth.refresh_access_token": Effect.CREATE,
}

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
        # Garden discovery and import reach the external garden registry.
        "tools.discover_servers",
        "tools.import_garden_servers",
        "tools.get_garden_status",
    }
)

#: Reads that require approval anyway, per FR-006g and FR-020.
#:
#: A read that discloses something a member would want to be asked about is not
#: a free call. Kept as data so the rule stays "not read-only, or named here".
APPROVAL_REQUIRED_READS: frozenset[str] = frozenset(
    {
        # Enumerates every secret held by the platform. The values are absent,
        # but the inventory itself is disclosure.
        "catalog.list_secrets",
        # Enumerates members and their identity records.
        "auth.list_users",
        "auth.get_user",
        # Enumerates active personal access tokens.
        "auth.list_pats",
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
HINT_OVERRIDES: dict[str, BehaviourHints] = {
    # Returns the new key once and only once, so a retry is not equivalent to
    # the first call: the caller loses the key it did not read.
    "cluster.rotate_preshared_key": BehaviourHints(
        read_only=False, destructive=True, idempotent=False, open_world=False
    ),
}


@dataclass(frozen=True, slots=True)
class OperationDescriptor:
    """One operation with everything a host needs to present and gate it.

    Attributes:
        entry: The indexed operation this describes.
        effect: What the operation does to platform state.
        hints: Advisory behaviour hints.
        requires_approval: Whether a member must approve before the call runs.
        returns_credential: Whether the result carries a credential. Such an
            operation is withheld rather than published, so this records why.
        paginated: Whether the operation takes paging arguments.
    """

    entry: OperationEntry
    effect: Effect
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
_IDEMPOTENT_EFFECTS = frozenset({Effect.READ, Effect.UPDATE, Effect.DESTROY})


def classify(op_selector: str, method: str) -> Effect | None:
    """Return an operation's effect, or ``None`` when the verb is unknown.

    Args:
        op_selector: Dotted ``service.method`` selector, used to find an
            override before any derivation runs.
        method: Method name, whose leading token carries the verb.

    Returns:
        The effect, or ``None`` when the leading verb is in no verb set. A
        ``None`` is a gap to be closed in :data:`EFFECT_OVERRIDES`, never a
        silent default to read.
    """
    override = EFFECT_OVERRIDES.get(op_selector)
    if override is not None:
        return override
    verb = method.split("_")[0].lower()
    if verb in _READ_VERBS:
        return Effect.READ
    if verb in _CREATE_VERBS:
        return Effect.CREATE
    if verb in _UPDATE_VERBS:
        return Effect.UPDATE
    if verb in _DESTROY_VERBS:
        return Effect.DESTROY
    return None


def _derive_hints(effect: Effect, op_selector: str) -> BehaviourHints:
    """Derive behaviour hints from an effect, before any override applies.

    A create is not idempotent because calling it twice makes two things. An
    update or a destroy is, because the second call finds the work already done.

    Args:
        effect: The operation's effect.
        op_selector: Dotted selector, checked against the open-world allowlist.

    Returns:
        The derived hints.
    """
    return BehaviourHints(
        read_only=effect.is_read,
        destructive=effect is Effect.DESTROY,
        idempotent=effect in _IDEMPOTENT_EFFECTS,
        open_world=op_selector in OPEN_WORLD_OPERATIONS,
    )


def describe(entry: OperationEntry) -> OperationDescriptor:
    """Build the descriptor for one indexed operation.

    Args:
        entry: An operation from the index.

    Returns:
        The descriptor, with hints derived from the effect and then replaced
        wholesale by an override when one is registered.

    Raises:
        ValueError: If the operation's verb is unclassified. Refusing here is
            deliberate: a descriptor that silently defaulted to read-only would
            publish a mutation as a free call.
    """
    effect = classify(entry.selector, entry.method)
    if effect is None:
        raise ValueError(
            f"{entry.selector} has an unclassified verb "
            f"{entry.method.split('_')[0]!r}; add it to a verb set or to "
            f"EFFECT_OVERRIDES"
        )
    hints = HINT_OVERRIDES.get(entry.selector) or _derive_hints(effect, entry.selector)
    requires_approval = not hints.read_only or entry.selector in APPROVAL_REQUIRED_READS
    return OperationDescriptor(
        entry=entry,
        effect=effect,
        hints=hints,
        requires_approval=requires_approval,
        returns_credential=not entry.is_published
        and "credential" in (entry.unpublished_reason or "").lower(),
        paginated=bool(_PAGING_PARAMETERS & set(entry.parameters)),
    )


def describe_all(index: OperationIndex) -> tuple[OperationDescriptor, ...]:
    """Describe every published operation in an index.

    Unpublished operations are skipped: they are never presented to a host, and
    one of them carrying an unclassifiable verb must not break the surface.

    Args:
        index: The operation index.

    Returns:
        Descriptors in index order.

    Raises:
        ValueError: If a published operation has an unclassified verb.
    """
    return tuple(describe(entry) for entry in index.published)


def unclassified(index: OperationIndex) -> tuple[str, ...]:
    """Return the selectors whose verb no rule classifies.

    The docstring and coverage gates call this rather than discovering the gap
    by crashing at describe time.

    Args:
        index: The operation index.

    Returns:
        Selectors of published operations with an unclassified verb, in index
        order. Empty when every operation classifies.
    """
    return tuple(
        entry.selector
        for entry in index.published
        if classify(entry.selector, entry.method) is None
    )
