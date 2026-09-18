"""Hint safety: a published read must not be a write in disguise.

Every assertion here reads the operation's own request line out of its source,
so it cannot pass by comparing one hint table against another. That is how
``connectors.verify_connection`` shipped as an approval-free read while its own
docstring said, in bold, that it writes.
"""

from __future__ import annotations

import warnings
from dataclasses import replace

import pytest

from kamiwaza_sdk.agent_tools.descriptors import (
    HINT_OVERRIDES,
    OPEN_WORLD_OPERATIONS,
    BehaviourHints,
    _request_signature,
    derive_hints,
    describe,
    describe_all,
    resolve_service,
    unknown_verbs,
)
from kamiwaza_sdk.agent_tools.envelopes import CallContext, platform_fault
from kamiwaza_sdk.agent_tools.ids import UNPUBLISHED
from kamiwaza_sdk.agent_tools.schemas import underivable
from kamiwaza_sdk.agent_tools.spec_index import build_index

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def client():
    """A client built against a local URL, never called."""
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return KamiwazaClient(base_url="http://localhost:7777/api")


@pytest.fixture(scope="module")
def index(client):
    """The operation index for that client."""
    return build_index(client)


#: HTTP methods that change state on this platform.
_STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Published reads that issue a POST because the query takes a request body.
#:
#: This platform posts a body for search and retrieval, so a POST alone does
#: not prove a write. Each entry below was read and carries the reason it is a
#: query rather than a change. Anything not named here fails the sweep: the
#: list is the whole exemption, so a new read-only POST has to be justified
#: here before it can ship.
_QUERY_STYLE_POSTS: dict[str, str] = {
    "authz.check_access": "Evaluates policy for a subject and returns the verdict.",
    "context.agentic_search": "Unified search; the body carries the query.",
    "context.evaluate_import_options": (
        "Validates source descriptors against import rules and returns options."
    ),
    "context.get_memory": "Reads an ontology's memory; the body scopes the read.",
    "context.query_vectors": "Vector similarity query against one vector database.",
    "context.query_vectors_global": "The same query across every vector database.",
    "context.retrieve": "Retrieval query; the body carries the question.",
    "context.search": "Context search; the body carries the query.",
    "context.search_knowledge": "Ontology search; the body carries the query.",
    "models.search_hub_model_files": "Searches the model hub's file listing.",
    "models.search_models": "Searches the model catalog; the body carries filters.",
    "serving.estimate_model_vram": (
        "Computes a VRAM estimate from the posted deployment shape; stores nothing."
    ),
}

#: Published reads whose request line ``_request_signature`` cannot see.
#:
#: The sweep below reads the HTTP verb out of the method's own source, so an
#: operation that issues its request from somewhere else — a shared base-class
#: helper, a streaming transport, a path built at call time — yields no
#: signature and is skipped rather than checked. Skipped is not checked, so the
#: set is written down: measured on this branch, 24 of the 163 published reads,
#: nine of them the catalogue's by-URN reads whose request lines moved into
#: ``_ByUrnClient`` in ``services/catalog.py``.
#:
#: Each was read by hand and none of them writes. The test asserts the set
#: exactly, in both directions: a new read that hides its request line has to
#: be added here deliberately, and one that becomes readable has to be removed
#: so the sweep starts covering it.
_REQUEST_LINE_UNSEEN: frozenset[str] = frozenset(
    {
        "agents.list",
        "apps.find_template",
        "catalog.containers.get",
        "catalog.containers.list",
        "catalog.datasets.get",
        "catalog.datasets.get_schema",
        "catalog.datasets.list",
        "catalog.get_dataset",
        "catalog.list_containers",
        "catalog.list_datasets",
        "catalog.list_secrets",
        "connectors.browse_surface",
        "connectors.search_surface",
        "federations.list",
        "kaizen_ops.get_model_settings",
        "models.check_download_status",
        "models.filter_compatible_models",
        "models.get_model_by_repo_id",
        "models.get_model_download_status",
        "retrieval.flight_batches",
        "retrieval.slack_messages",
        "retrieval.stream_job",
        "serving.list_active_deployments",
        "serving.stream_deployment_logs",
    }
)


def _request_verb(client, selector: str, method: str) -> tuple[str, str] | None:
    """Return the HTTP method and path the operation issues, if it is literal.

    Args:
        client: The client the index was built from.
        selector: Dotted ``service.method`` selector.
        method: The method's name on its service.

    Returns:
        ``(http_method, path)``, or ``None`` when the method builds its request
        dynamically and no literal call can be read from the source.
    """
    service = resolve_service(client, selector.rsplit(".", 1)[0])
    if service is None:
        return None
    return _request_signature(service, method)


def test_no_published_read_issues_a_state_changing_request(index, client) -> None:
    """The sweep the review found missing, run over the whole published set.

    Hints come from the method name. This reads the request line out of the
    body instead, so a name that sounds like a read while the body posts is a
    failure rather than an approval-free operation.
    """
    offenders = []
    for descriptor in describe_all(index):
        if not descriptor.hints.read_only:
            continue
        signature = _request_verb(client, descriptor.selector, descriptor.entry.method)
        if signature is None or signature[0] not in _STATE_CHANGING:
            continue
        if signature[0] == "POST" and descriptor.selector in _QUERY_STYLE_POSTS:
            continue
        offenders.append(f"{descriptor.selector} issues {signature[0]} {signature[1]}")
    assert offenders == [], (
        "published as read_only while issuing a state-changing request. A "
        "query-style POST belongs in _QUERY_STYLE_POSTS with its reason; "
        f"anything else is a wrong hint: {offenders}"
    )


def test_the_sweeps_blind_spot_is_exactly_the_named_set(index, client) -> None:
    """A read the sweep cannot see passes it silently, so name every one.

    The sweep skips an operation whose request line is not in its own source.
    Without this the skipped set is free to grow with every refactor that moves
    a request into a helper, and nothing says which reads are unchecked.
    """
    unseen = {
        descriptor.selector
        for descriptor in describe_all(index)
        if descriptor.hints.read_only
        and _request_verb(client, descriptor.selector, descriptor.entry.method)
        is None
    }
    added = sorted(unseen - _REQUEST_LINE_UNSEEN)
    removed = sorted(_REQUEST_LINE_UNSEEN - unseen)
    assert not added, (
        "these published reads no longer show the sweep a request line, so it "
        "cannot check them. Read each one and add it to _REQUEST_LINE_UNSEEN, "
        f"or give it a literal request call: {added}"
    )
    assert not removed, (
        "the sweep can now read these, so they are checked and their exemption "
        f"is stale. Delete them from _REQUEST_LINE_UNSEEN: {removed}"
    )


def test_the_query_style_exemptions_are_all_still_read_only_posts(
    index, client
) -> None:
    """A stale exemption would hide the next wrong hint on that selector."""
    described = {d.selector: d for d in describe_all(index)}
    for selector, reason in _QUERY_STYLE_POSTS.items():
        assert reason, f"{selector} is exempt without a stated reason"
        descriptor = described.get(selector)
        assert descriptor is not None, f"{selector} is no longer published"
        assert descriptor.hints.read_only, (
            f"{selector} no longer publishes as a read, so its exemption is stale"
        )
        signature = _request_verb(client, selector, descriptor.entry.method)
        assert signature is not None and signature[0] == "POST", (
            f"{selector} no longer posts, so its exemption is stale"
        )


def test_verify_connection_publishes_as_an_open_world_write(index) -> None:
    """Its docstring says "This writes, despite the name"; the hints must agree.

    ``verify`` used to sit in the read verb set for this one operation. The
    platform persists the connection health the probe produces, so a call can
    move a member's stored connection into degraded or reauth-required, and the
    probe reaches the third-party provider.
    """
    descriptor = next(
        d for d in describe_all(index) if d.selector == "connectors.verify_connection"
    )
    assert not descriptor.hints.read_only
    assert descriptor.requires_approval
    assert descriptor.hints.open_world
    assert derive_hints("connectors.verify_connection", "verify_connection") is None, (
        "'verify' must classify nothing, so a future verify_* cannot inherit a read"
    )


def test_verify_connection_does_not_publish_as_retry_safe(index) -> None:
    """Its docstring forbids polling, and ``idempotent`` is published as retry safety.

    ``envelopes.platform_fault`` carries ``idempotent`` to a host as
    ``safe_to_retry``. Declaring it on an operation whose docstring says "do
    not poll it on a timer or fan it out across a catalog" invites exactly the
    automatic retry the docstring warns against.
    """
    descriptor = next(
        d for d in describe_all(index) if d.selector == "connectors.verify_connection"
    )
    assert not descriptor.hints.idempotent
    context = CallContext(
        status="502",
        code="provider_unreachable",
        timeout_ms=5_000,
        source="platform",
        request_id="req-1",
    )
    failure = platform_fault(
        "the provider did not answer", context, idempotent=descriptor.hints.idempotent
    )
    assert failure.detail == {"safe_to_retry": False}


def test_gates_discover_publishes_as_a_write(index) -> None:
    """``POST /authz/gates/discover`` imports a caller-supplied classpath.

    A server-side import runs that module's top-level code on the
    authorization surface, so it cannot publish as a free read.
    """
    descriptor = next(d for d in describe_all(index) if d.selector == "gates.discover")
    assert not descriptor.hints.read_only
    assert descriptor.requires_approval
    assert derive_hints("gates.discover", "discover") is None, (
        "'discover' must classify nothing, so a future discover_* fails closed"
    )


def test_a_dropped_read_verb_still_fails_closed() -> None:
    """Removing a verb must gate the next operation that uses it, not free it."""
    entry_hints = describe(
        _entry("connectors.verify_surface", "connectors", "verify_surface")
    ).hints
    assert not entry_hints.read_only
    assert entry_hints.destructive
    assert not entry_hints.idempotent


def test_unknown_verbs_reports_nothing(index) -> None:
    """Both dropped verbs are stated per operation, so the report stays empty."""
    assert unknown_verbs(index) == ()


def test_an_override_cannot_clear_the_open_world_flag(index, monkeypatch) -> None:
    """An override states three hints; it must not close open_world by omission.

    ``BehaviourHints`` has no default for ``open_world``, so every override
    writes a value for it. An author correcting ``idempotent`` on an
    allowlisted operation would have to remember the allowlist to keep the flag
    — which is the kind of thing nobody remembers.
    """
    selector = "apps.pull_images"
    assert selector in OPEN_WORLD_OPERATIONS
    entry = next(e for e in index if e.selector == selector)
    assert describe(entry).hints.open_world

    monkeypatch.setitem(
        HINT_OVERRIDES,
        selector,
        BehaviourHints(
            read_only=False, destructive=False, idempotent=True, open_world=False
        ),
    )
    assert describe(entry).hints.open_world, (
        "an override closed the open-world flag on an allowlisted operation"
    )
    assert describe(entry).hints.idempotent, "the override's own hints must still win"


def test_openai_get_client_is_withheld(index) -> None:
    """It hands back a local object, like the other withheld factories.

    ``embedding.get_embedder``, ``subjects.grants`` and ``federations.by_id``
    are withheld for exactly this: an agent receives an object it cannot
    invoke across a tool boundary.
    """
    reason = UNPUBLISHED.get("openai.get_client")
    assert reason is not None
    assert "local" in reason.reason.lower()
    published = {e.selector for e in index if e.is_published}
    assert "openai.get_client" not in published
    assert "openai.get_client" not in {d.selector for d in describe_all(index)}


def test_every_secret_operation_is_gated(index) -> None:
    """The stated rationale is disclosure, which a single record carries too.

    ``catalog.list_secrets`` and ``catalog.secrets.list`` were gated on the
    inventory being disclosure. ``catalog.secrets.get`` reads one record by
    URN, so the gate was avoidable by anyone who could name a secret.
    """
    ungated = [
        d.selector
        for d in describe_all(index)
        if "secret" in d.selector and not d.requires_approval
    ]
    assert ungated == []


def test_a_presigned_download_url_is_gated(index) -> None:
    """The URL carries its own authority, so the read hands out a capability."""
    descriptor = next(
        d
        for d in describe_all(index)
        if d.selector == "context.get_document_download_url"
    )
    assert descriptor.requires_approval


def _reaches_the_registry(path: str) -> bool:
    """Whether a request line reaches the App Garden registry.

    Args:
        path: The request path the method's own source issues, or ``""`` when
            the method issues none this test can read.

    Returns:
        Whether the path names the garden or the remote catalogue. Both
        spellings appear: the import routes carry ``garden`` and the listing
        route carries ``remote``.
    """
    return "garden" in path or "remote" in path


def test_garden_operations_publish_as_open_world(index, client) -> None:
    """The published garden path reaches the same registry as the withheld one.

    The allowlist named the withheld ``tools.*`` garden operations only, so a
    host reading the published ``apps.*`` ones saw a closed-world call to an
    external registry.
    """
    closed = []
    for descriptor in describe_all(index):
        signature = _request_verb(client, descriptor.selector, descriptor.entry.method)
        path = signature[1] if signature is not None else ""
        if _reaches_the_registry(path) and not descriptor.hints.open_world:
            closed.append(f"{descriptor.selector} calls {path}")
    assert closed == []

    install = next(
        d for d in describe_all(index) if d.selector == "apps.install_by_name"
    )
    assert install.hints.open_world, (
        "install_by_name imports the garden catalog when the template is "
        "missing locally, so it reaches the registry even with no literal "
        "request line of its own"
    )




def test_the_schema_gate_reaches_nested_operations(index, client) -> None:
    """A nested operation must be able to fail the schema gate.

    ``underivable`` resolved the service with one ``getattr`` for the whole
    dotted name, which is never an attribute, so every nested operation looked
    like an absent service and was skipped instead of reported. 35 of the 335
    published operations live on a nested sub-client.

    The entry below names a method the sub-client does not have, which is the
    cheapest thing the gate must report: no annotations can be read from a
    method that is not there.
    """
    entry = _entry("catalog.secrets.frobnicate", "catalog.secrets", "frobnicate")
    assert resolve_service(client, "catalog.secrets") is not None
    one_operation = replace(index, entries=(entry,))
    assert underivable(one_operation, client) == ("catalog.secrets.frobnicate",)


def _entry(selector: str, service: str, method: str):
    """Build an index entry for an operation this client does not have.

    Args:
        selector: Dotted ``service.method`` selector.
        service: Service attribute name.
        method: Method name on that service.

    Returns:
        An ``OperationEntry`` with no parameters and no docstring, which is
        enough for hint derivation.
    """
    from kamiwaza_sdk.agent_tools.spec_index import OperationEntry

    return OperationEntry(
        selector=selector,
        published_id=method,
        service=service,
        method=method,
        summary=None,
        parameters=(),
        required_parameters=(),
        returns=None,
    )
