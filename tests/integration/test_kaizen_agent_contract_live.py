"""Live contract test for canonical Kaizen agent creation through the SDK.

Unit tests pin the request body the SDK builds; only a live call proves the
server accepts it. This is the test that would have caught the seeding failure
where the SDK posted the legacy flat body to canonical Kaizen and got an
HTTP 422 back.

Skips (never fails) when no canonical Kaizen instance is reachable — most live
hosts don't run one, and a red suite there would say nothing about the contract.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import KamiwazaError
from kamiwaza_sdk.schemas.kaizen import AgentDefinition
from kamiwaza_sdk.seeding.client import scoped_client_for_workroom
from kamiwaza_sdk.services.kaizen import (
    CANONICAL_EXTENSION_NAME,
    AmbiguousExtensionError,
    _is_serving,
    resolve_base_url,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
]


def _canonical_kaizen(client):
    """Find (scoped_client, workroom_id, base_url) for a canonical Kaizen, or None.

    Matches on the canonical catalog identity specifically: while legacy Kaizen
    still ships as ``kaizen-legacy``, a workroom can hold either or both, and
    the two answer different agent-create contracts.

    Scoping into each candidate is unavoidable rather than careless: the
    platform only lists a workroom's extensions to a caller already scoped into
    it, so there is no unscoped read that could narrow the search first. The
    loop therefore stops at the first canonical instance it finds.
    """
    try:
        workrooms = client.workrooms.list()
    except KamiwazaError:
        return None
    for workroom in workrooms:
        workroom_id = str(workroom.id)
        try:
            scoped = scoped_client_for_workroom(client, workroom_id)
            base_url = resolve_base_url(
                scoped,
                CANONICAL_EXTENSION_NAME,
                workroom_id=workroom_id,
            )
        except (KamiwazaError, AmbiguousExtensionError, ValueError):
            continue
        # A published ingress only means envoy has a route; right after install
        # the backend can still be starting and every call 503s. Without this
        # the tests would FAIL on a cold box, contradicting the skip-never-fail
        # contract in this module's docstring.
        if not _is_serving(scoped, base_url, workroom_id=workroom_id):
            continue
        return scoped, workroom_id, base_url
    return None


@pytest.fixture
def canonical_kaizen(live_kamiwaza_client):
    found = _canonical_kaizen(live_kamiwaza_client)
    if found is None:
        pytest.skip(
            f"no reachable '{CANONICAL_EXTENSION_NAME}' extension in any workroom"
        )
    return found


def test_canonical_agent_create_accepts_the_content_contract(canonical_kaizen):
    client, workroom_id, base_url = canonical_kaizen
    # Instance-unique names, so a re-run can't collide with a leftover agent.
    name = f"sdk-contract-{uuid4().hex[:8]}"
    definition = AgentDefinition(
        name=name,
        persona="You are a contract-test agent. Answer briefly.",
        description="Created by the SDK canonical Kaizen contract test.",
    )

    agent = client.agents.create_canonical(
        definition, base_url=base_url, workroom_id=workroom_id
    )
    try:
        # The stable agent-id output the seeder threads into chat verification.
        assert agent.id
        assert agent.version is not None
        listed = client.agents.list(base_url=base_url, workroom_id=workroom_id)
        assert any(item.id == agent.id for item in listed)
    finally:
        client.agents.delete(agent.id, base_url=base_url, workroom_id=workroom_id)


def test_ops_model_settings_carry_the_shape_bind_chat_model_reads(canonical_kaizen):
    """Anchor the response shape `bind-chat-model` confirms itself against.

    `cmd_bind_chat_model` decides whether automation may proceed by reading
    `chat.current.id` out of the ops model-settings view. That is the seeder's
    load-bearing step, and until something checks it against a real instance
    it is an assumption. This reads the same view `set_chat_model` returns.

    Deliberately read-only: repointing a live instance's chat model would
    mutate operator config other tests and users on that box depend on, so the
    write half stays covered by unit tests and by the seeder itself.
    """
    client, workroom_id, base_url = canonical_kaizen

    settings = client.kaizen_ops.get_model_settings(
        base_url=base_url, workroom_id=workroom_id
    )

    assert isinstance(settings, dict), f"expected a settings object, got {type(settings)}"
    assert "chat" in settings, f"no 'chat' key in ops model settings: {sorted(settings)}"
    chat = settings["chat"]
    assert isinstance(chat, dict), f"expected 'chat' to be an object, got {type(chat)}"
    # `current` is None when nothing is bound yet — a legitimate state on a
    # fresh instance. What must hold is that when it IS bound, the id lives at
    # chat.current.id, which is what the CLI reads.
    current = chat.get("current")
    assert current is None or (
        isinstance(current, dict) and "id" in current
    ), f"chat.current is neither None nor an object carrying 'id': {current!r}"


def test_ops_model_settings_carry_the_inventory_embedding_discovery_reads(
    canonical_kaizen,
):
    """Anchor the shape `bind-embedding-model` discovers a deployment from.

    With no `--deployment-id`, the command picks one out of `embedding_models`
    and binds it, so both the list and the `id` / `capabilities` fields on its
    entries are load-bearing. Nothing else checks them against a real instance.

    Read-only for the same reason as the chat anchor: repointing a live
    instance's embedding model would re-target retrieval for everyone on the
    box. An empty inventory is a legitimate state here — it is exactly what
    the command refuses to proceed past — so this asserts the shape, not that
    the environment happens to serve an embedding model.
    """
    client, workroom_id, base_url = canonical_kaizen

    settings = client.kaizen_ops.get_model_settings(
        base_url=base_url, workroom_id=workroom_id
    )

    assert "embedding" in settings, (
        f"no 'embedding' key in ops model settings: {sorted(settings)}"
    )
    current = settings["embedding"].get("current")
    assert current is None or (
        isinstance(current, dict) and "id" in current
    ), f"embedding.current is neither None nor an object carrying 'id': {current!r}"

    inventory = settings.get("embedding_models")
    assert isinstance(inventory, list), (
        f"expected 'embedding_models' to be a list, got {type(inventory)}"
    )
    for entry in inventory:
        assert isinstance(entry, dict), f"inventory entry is not an object: {entry!r}"
        assert entry.get("id"), f"inventory entry carries no deployment id: {entry!r}"
        # Absent capabilities is tolerated by discovery (the instance already
        # typed the entry as an embedding model); a non-list is not, because it
        # would silently never match the capability preference.
        capabilities = entry.get("capabilities")
        assert capabilities is None or isinstance(capabilities, list), (
            f"'capabilities' is neither absent nor a list: {capabilities!r}"
        )


def test_canonical_agent_create_rejects_the_legacy_body(canonical_kaizen):
    """The legacy contract must fail loudly here, not create a stray agent.

    This is the other half of "legacy cannot be confused with canonical": if
    canonical ever started accepting the flat body, silent selection would stop
    being detectable and the SDK's identity-based dispatch could rot unnoticed.
    """
    client, workroom_id, base_url = canonical_kaizen
    created = None

    try:
        with pytest.raises(KamiwazaError) as excinfo:
            created = client.agents.create(
                base_url=base_url,
                name=f"sdk-contract-legacy-{uuid4().hex[:8]}",
                llm={"model": "any", "base_url": "https://unused.example/v1"},
                workroom_id=workroom_id,
            )
        assert getattr(excinfo.value, "status_code", None) in (400, 422)
    finally:
        # If canonical ever *did* accept the flat body, the assertion above
        # fails — and without this the stray agent would leak onto a live
        # instance, which is the very outcome this test exists to detect.
        if created is not None:
            client.agents.delete(
                created.id, base_url=base_url, workroom_id=workroom_id
            )
