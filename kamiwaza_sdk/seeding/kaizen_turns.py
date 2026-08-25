# kamiwaza_sdk/seeding/kaizen_turns.py

"""Kaizen conversation turns, one implementation per catalog identity.

Two Kaizen products ship side by side and diverge across the whole turn, not
just at create: canonical (``kaizen``) has no ``/messages`` and no ``/run``
route at all and streams its events as SSE, while legacy (``kaizen-legacy``)
uses the v3 create body, a sandbox readiness wait, and paginated JSON events.
Crossing them is an HTTP 422 or 404, never a degraded success.

Kept out of ``cli.py`` so the command module stays about argument handling and
this one owns the contract split.
"""

import argparse
from typing import Optional, Tuple

from ..services.kaizen import (
    AGENT_CONTRACT_CANONICAL,
    AGENT_CONTRACT_LEGACY,
    agent_contract_for_extension,
)

def conversation_contract(args: argparse.Namespace) -> str:
    """Resolve the turn contract from the catalog identity, or fail locally.

    Same selection ENG-10847 established for agents: the identity decides the
    body shape, so an operator never has to know which one a given Kaizen wants
    and a mismatch is a local error with the fix in it, not an opaque HTTP 422.
    """
    try:
        return agent_contract_for_extension(args.extension_name)
    except ValueError as exc:
        raise SystemExit(str(exc))


def _create_canonical_conversation(args: argparse.Namespace, client):
    """Open a conversation on canonical Kaizen (no body; Idempotency-Key)."""
    return client.conversations.create_canonical(
        base_url=args.kaizen_base_url,
        workroom_id=args.workroom_id,
    )


def _create_legacy_conversation(args: argparse.Namespace, client):
    """Open a conversation on legacy Kaizen (v3 flat body)."""
    return client.conversations.create(
        base_url=args.kaizen_base_url,
        agent_id=args.agent_id,
        title=args.title,
        max_iterations=args.max_iterations,
        ephemeral=args.ephemeral,
        workroom_id=args.workroom_id,
    )


def _chat_canonical(args: argparse.Namespace, client) -> Tuple[str, Optional[str]]:
    """Run one canonical-Kaizen turn: open a conversation, send, read the reply.

    Canonical Kaizen selects the agent per input and exposes no sandbox status,
    so there is nothing to wait on between create and send.
    """
    conversation = client.conversations.create_canonical(
        base_url=args.kaizen_base_url,
        workroom_id=args.workroom_id,
    )
    reply = client.conversations.chat_canonical(
        conversation.id,
        args.message,
        base_url=args.kaizen_base_url,
        workroom_id=args.workroom_id,
        agent=args.agent_id,
        # The CLI spells fire-and-forget as `--timeout 0`; the canonical client
        # spells it None (no event stream opened at all).
        timeout_seconds=args.timeout or None,
    )
    return conversation.id, reply


def _chat_legacy(args: argparse.Namespace, client) -> Tuple[str, Optional[str]]:
    """Run one legacy (v3) Kaizen turn: create, await sandbox, send, poll."""
    conversation = client.conversations.create(
        base_url=args.kaizen_base_url,
        agent_id=args.agent_id,
        title=args.title,
        workroom_id=args.workroom_id,
    )
    if args.timeout:
        client.conversations.wait_until_ready(
            conversation.id,
            base_url=args.kaizen_base_url,
            workroom_id=args.workroom_id,
            timeout_seconds=args.sandbox_timeout,
            poll_interval_seconds=args.poll_interval,
        )
    reply = client.conversations.chat(
        conversation.id,
        args.message,
        base_url=args.kaizen_base_url,
        workroom_id=args.workroom_id,
        timeout_seconds=args.timeout,
        poll_interval_seconds=args.poll_interval,
    )
    return conversation.id, reply


# Contract -> turn implementation. A table rather than a branch in each command:
# the identity is validated once by `agent_contract_for_extension`, so a missing
# key here would be a programming error, not an operator one.
CREATE_CONVERSATION_BY_CONTRACT = {
    AGENT_CONTRACT_CANONICAL: _create_canonical_conversation,
    AGENT_CONTRACT_LEGACY: _create_legacy_conversation,
}

CHAT_TURN_BY_CONTRACT = {
    AGENT_CONTRACT_CANONICAL: _chat_canonical,
    AGENT_CONTRACT_LEGACY: _chat_legacy,
}
