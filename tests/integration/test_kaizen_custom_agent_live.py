"""Live producer candidate for canonical Kaizen custom agents with a skill binding.

This intentionally does not emit capability evidence until it passes on a
configured live platform. A create/list-only probe is insufficient for the claim.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from kamiwaza_sdk.schemas.kaizen import AgentDefinition

from .test_kaizen_agent_contract_live import _canonical_kaizen

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_canonical_agent_binds_published_library_skill(
    live_kamiwaza_client,
) -> None:
    found = _canonical_kaizen(live_kamiwaza_client)
    if found is None:
        pytest.skip("No reachable canonical Kaizen extension in any workroom")
    client, workroom_id, base_url = found
    published = live_kamiwaza_client.skills.list_skills(
        status="published", page_size=20
    )
    if not published.items:
        pytest.skip("No published Skills Library skill available for Kaizen binding")

    definition = AgentDefinition(
        name=f"sdk-custom-agent-{uuid4().hex[:8]}",
        persona="Use the bound skill when it is relevant.",
    )
    agent = client.agents.create_canonical(
        definition, base_url=base_url, workroom_id=workroom_id
    )
    try:
        skill_id = published.items[0].id
        bound = client.agents.bind_skill(
            agent.id, skill_id, base_url=base_url, workroom_id=workroom_id
        )
        assert bound.id == agent.id
        listed = client.agents.list(base_url=base_url, workroom_id=workroom_id)
        current = next(item for item in listed if item.id == agent.id)
        bindings = getattr(current, "skill_bindings", None)
        assert bindings, "Agent listing did not retain the skill binding"
        assert str(skill_id) in str(bindings)
    finally:
        client.agents.delete(agent.id, base_url=base_url, workroom_id=workroom_id)
