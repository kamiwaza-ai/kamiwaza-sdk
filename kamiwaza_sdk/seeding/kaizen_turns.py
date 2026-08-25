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
    CANONICAL_EXTENSION_NAME,
    LEGACY_EXTENSION_NAME,
    agent_contract_for_extension,
)

# Flags that only the v3 conversation body can carry. Canonical Kaizen creates a
# conversation with no body at all and selects the agent per input, so it has
# nowhere to put any of these — accepting them there would drop an operator's
# choice silently, which is the failure mode this contract split exists to
# remove.
_LEGACY_ONLY_CONVERSATION_FLAGS = (
    ("--agent-id", "agent_id"),
    ("--title", "title"),
    ("--max-iterations", "max_iterations"),
)


def _reject_legacy_conversation_flags(args: argparse.Namespace) -> None:
    """Fail loudly when a canonical create carries v3-only conversation flags."""
    supplied = [
        flag
        for flag, dest in _LEGACY_ONLY_CONVERSATION_FLAGS
        if getattr(args, dest, None) is not None
    ]
    if getattr(args, "ephemeral", False):
        supplied.append("--ephemeral")
    if not supplied:
        return
    joined = ", ".join(supplied)
    verb = "is a v3 conversation setting" if len(supplied) == 1 else "are v3 conversation settings"
    raise SystemExit(
        f"{joined} {verb} that canonical Kaizen "
        f"('{CANONICAL_EXTENSION_NAME}') does not support: it creates a "
        "conversation with no body and selects the agent per message. Pass "
        f"--extension-name {LEGACY_EXTENSION_NAME} to use the legacy contract."
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
    _reject_legacy_conversation_flags(args)
    return client.conversations.create_canonical(
        base_url=args.kaizen_base_url,
        workroom_id=args.workroom_id,
    )


def _create_legacy_conversation(args: argparse.Namespace, client):
    """Open a conversation on legacy Kaizen (v3 flat body)."""
    if not args.agent_id:
        raise SystemExit(
            f"--agent-id is required for legacy Kaizen "
            f"('{LEGACY_EXTENSION_NAME}') conversations."
        )
    return client.conversations.create(
        base_url=args.kaizen_base_url,
        agent_id=args.agent_id,
        title=args.title,
        # The v3 server default; kept here so the flag can stay unset-by-default
        # and the canonical path can tell "operator asked for this" from "never
        # supplied".
        max_iterations=args.max_iterations if args.max_iterations is not None else 500,
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
