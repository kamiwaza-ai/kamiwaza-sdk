"""Operation identifiers for the agent tool surface.

Every operation carries two identifiers, per the MCP server's FR-005d:

* a **selector** in dotted ``service.method`` form, used internally and in the
  unpublished set; stable against route renames because it names the client
  method rather than an HTTP path.
* a **published identifier** derived from the selector by
  :func:`published_id`, which places the verb first in snake_case. This is what
  an agent host sees.

The published form is derived rather than stored so that a method added to the
client is reachable the day it ships, with no curation step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "UnpublishedOperationError",
    "UNPUBLISHED",
    "UnpublishedReason",
    "published_id",
    "selector",
    "unpublished_reason",
]

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


class UnpublishedOperationError(LookupError):
    """Raised when an agent asks for an operation that is deliberately unpublished.

    Carries the stated reason rather than reporting the operation as unknown, so
    a caller can tell "not offered, because X" from "does not exist". An agent
    that cannot make that distinction invents a workaround.
    """

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(f"{operation} is not published: {reason}")
        self.operation = operation
        self.reason = reason


@dataclass(frozen=True, slots=True)
class UnpublishedReason:
    """Why one operation is withheld, and what to use instead.

    Attributes:
        reason: Prose stating why the operation is not published. Reaches the
            caller verbatim through :class:`UnpublishedOperationError`.
        superseded_by: Selector of the operation that replaces this one, when
            one exists.
    """

    reason: str
    superseded_by: str | None = None

    def message(self) -> str:
        """Return the reason, naming the replacement operation when there is one."""
        if self.superseded_by is None:
            return self.reason
        return f"{self.reason} Use {self.superseded_by} instead."


def _tokens(name: str) -> list[str]:
    """Split an identifier into lowercase word tokens.

    Handles ``snake_case``, ``camelCase`` and ``PascalCase``, so a client that
    mixes conventions still yields one canonical token sequence.

    Args:
        name: Identifier to split.

    Returns:
        Lowercase tokens in source order, with empty segments dropped.
    """
    spaced = _CAMEL_BOUNDARY.sub("_", name)
    return [t for t in spaced.lower().split("_") if t]


def _singular(token: str) -> str:
    """Return a crude singular form of one token, for namespace de-duplication.

    Only used to decide whether a namespace token is already implied by the
    method name, so an approximation is adequate and a wrong answer costs a
    slightly longer identifier rather than a wrong one.

    Args:
        token: Lowercase word token.

    Returns:
        The token without a trailing plural ``s``/``es``, when removing it
        leaves at least three characters.
    """
    for suffix in ("ies", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            if suffix == "ies":
                return f"{token[: -len(suffix)]}y"
            return token[: -len(suffix)]
    return token


def selector(service: str, method: str) -> str:
    """Return the dotted internal selector for one client method.

    Args:
        service: Attribute name of the service on the client, such as ``models``.
        method: Method name on that service, such as ``list_models``.

    Returns:
        The selector in ``service.method`` form.
    """
    return f"{service}.{method}"


def published_id(op_selector: str) -> str:
    """Derive the published identifier for a selector, verb first in snake_case.

    The method name supplies the leading tokens because it already begins with
    the verb in this client's convention. Namespace tokens are appended only
    when the method name does not already imply them, so ``models.list_models``
    publishes as ``list_models`` rather than ``list_models_models``.

    Args:
        op_selector: Selector in dotted ``service.method`` form, with any number
            of namespace segments before the method.

    Returns:
        The published identifier in snake_case.

    Raises:
        ValueError: If the selector has no namespace segment or no method
            segment, which would make the derivation ambiguous.

    Examples:
        >>> published_id("models.list_models")
        'list_models'
        >>> published_id("serving.deploy_model")
        'deploy_model'
        >>> published_id("cluster.get_node_by_id")
        'get_node_by_id_cluster'
    """
    parts = op_selector.split(".")
    if len(parts) < 2 or not all(parts):
        raise ValueError(
            f"selector must be dotted service.method form, got {op_selector!r}"
        )
    namespace_tokens = [t for part in parts[:-1] for t in _tokens(part)]
    method_tokens = _tokens(parts[-1])
    implied = {_singular(t) for t in method_tokens}
    tail = [t for t in namespace_tokens if _singular(t) not in implied]
    return "_".join(method_tokens + tail)


#: Operations deliberately not published, each with a stated reason.
#:
#: Two families, per FR-005e, both read from the client rather than assumed.
#:
#: ``tools`` is :class:`~kamiwaza_sdk.services.tools.ToolService`, whose own
#: class docstring carries a ``.. deprecated::`` notice: the Docker
#: Compose-based Tool Shed is replaced by CRD-based extensions, and callers are
#: directed to :class:`~kamiwaza_sdk.services.extensions.ExtensionService`.
#: Publishing a deprecated surface beside its replacement would offer an agent
#: two rival ways to do the same thing, and it would pick by description text.
#:
#: The rest return a credential in their result. FR-020 treats a
#: credential-bearing result as a mutation regardless of verb; withholding them
#: goes further, because a credential that reaches an agent can be replayed
#: outside any recorded call. Each was confirmed against its response model.
UNPUBLISHED: dict[str, UnpublishedReason] = {
    f"tools.{method}": UnpublishedReason(
        reason=(
            "ToolService is deprecated: the Docker Compose-based Tool Shed is "
            "replaced by CRD-based extensions."
        ),
        superseded_by="extensions",
    )
    for method in (
        "check_health",
        "deploy",
        "deploy_from_template",
        "discover_servers",
        "get_deployment",
        "get_garden_status",
        "import_garden_servers",
        "list_available_templates",
        "list_deployments",
        "list_imported_templates",
        "stop_deployment",
    )
} | {
    "auth.login_with_password": UnpublishedReason(
        reason=(
            "Performs a password grant and returns an access token. An agent "
            "authenticates as itself through the platform, never by exchanging "
            "a member's password."
        ),
    ),
    "auth.refresh_access_token": UnpublishedReason(
        reason=(
            "Exchanges a refresh token for an access token. Token lifecycle "
            "belongs to the platform's identity capability."
        ),
    ),
    "auth.create_pat": UnpublishedReason(
        reason=(
            "Mints a personal access token and returns its secret in the "
            "result, so a published call would hand an agent a replayable "
            "credential."
        ),
    ),
    "cluster.rotate_preshared_key": UnpublishedReason(
        reason=(
            "Returns the new pre-shared key once, and only once. A retry after "
            "a dropped result loses the key, which is not a shape an agent can "
            "hold safely."
        ),
    ),
}


def unpublished_reason(op_selector: str) -> UnpublishedReason | None:
    """Return why a selector is unpublished, or ``None`` when it is published.

    Args:
        op_selector: Selector in dotted ``service.method`` form.

    Returns:
        The recorded reason, or ``None`` if the operation is publishable.
    """
    return UNPUBLISHED.get(op_selector)
