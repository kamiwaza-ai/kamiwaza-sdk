"""UAC-11 §5 R2 relaxed acceptance — runtime-lib forwards request context.

D210 design §5 R2: when ENG-3822 (Platform Trust & Governed Execution v1.0
dispatch-level `model_invocation` audit event) does not land within the M3
window, UAC-11 closes against this **relaxed bar**: the runtime lib
correctly threads the full canonical user-identity envelope from the
incoming FastAPI request into the AsyncOpenAI client that the extension
will use to call the platform model-access boundary. The platform-side
dispatch attribution test (ENG-3905 in its full form) is deferred until
ENG-3822 ships and can be observed.

This file is the durable artifact for that relaxation. The full audit-event
verification — invoking through Traefik, capturing the dispatch event,
asserting end-user + workroom attribution — remains tracked under
ENG-3905's `blockedBy ENG-3822` edge and will run when ENG-3822 lands.

Refs:
- system design §4.4.5 Model-Invocation Attribution (revised 2026-04-23)
- system design §5 R2
- ENG-3822 (Platform Trust, John Strick) — gates the unrelaxed UAC-11 path
- ENG-3905 (D210 consumer test) — relaxed exit demonstrated here
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kamiwaza_extensions_lib.auth import _FORWARD_HEADERS
from kamiwaza_extensions_lib.models import get_model_client

# The full canonical envelope a Traefik-fronted request carries when the
# user is authenticated and bound to a workroom. Mirrors the IdentityExtractor
# input set (kamiwaza_extensions_lib.identity) so a future schema drift here
# fails this test loudly rather than silently dropping a header at the
# dispatch boundary.
CANONICAL_ENVELOPE = {
    "x-user-id": "usr-7c1e",
    "x-user-email": "alice@example.test",
    "x-user-name": "Alice Example",
    "x-user-roles": "member,reader",
    "x-user-system-high": "false",
    "x-user-workroom-role": "member",
    "x-workroom-id": "wr-9a3d",
    "x-auth-token": "jwt-platform-attested",
    "x-request-id": "req-deadbeef",
    "cookie": "session=opaque-session-id",
}


@pytest.mark.unit
class TestUAC11DispatchContextForwarding:
    """Each test pins one invariant the dispatch boundary will rely on."""

    @pytest.mark.asyncio
    async def test_every_canonical_envelope_header_reaches_dispatch_client(
        self, monkeypatch
    ):
        """The full identity envelope is threaded into the AsyncOpenAI client's
        default headers, so any model call through this client carries the
        end-user attribution the platform dispatch boundary needs."""
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "https://kamiwaza.test/api/v1")

        request = MagicMock()
        request.headers = dict(CANONICAL_ENVELOPE)

        client = await get_model_client(request)
        forwarded = getattr(client, "default_headers", None) or getattr(
            client, "_default_headers", {}
        )
        forwarded_lower = {k.lower(): v for k, v in forwarded.items()}

        for key, value in CANONICAL_ENVELOPE.items():
            assert key in forwarded_lower, (
                f"UAC-11 §5 R2: canonical envelope header {key!r} was dropped "
                f"between the incoming request and the AsyncOpenAI client. "
                f"This breaks end-user attribution at the platform dispatch "
                f"boundary (ENG-3822)."
            )
            assert forwarded_lower[key] == value, (
                f"UAC-11 §5 R2: header {key!r} reached the dispatch client "
                f"but was rewritten ({forwarded_lower[key]!r} vs {value!r}). "
                f"The runtime lib must pass the platform-attested envelope "
                f"through unmodified."
            )

    @pytest.mark.asyncio
    async def test_no_user_token_appears_as_dispatch_client_api_key(self, monkeypatch):
        """Per design §4.4.2 / §4.4.5 (revised 2026-04-23): no user-token
        passthrough to providers. When the incoming request has no
        ``Authorization`` header, the AsyncOpenAI api_key must be the
        runtime-lib's placeholder, not the platform attestation token."""
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "https://kamiwaza.test/api/v1")

        request = MagicMock()
        envelope = dict(CANONICAL_ENVELOPE)
        # No Authorization header — only the platform-attested envelope.
        request.headers = envelope

        client = await get_model_client(request)
        api_key = getattr(client, "api_key", None) or getattr(client, "_api_key", None)

        # When there's no incoming Authorization the placeholder is used.
        assert api_key == "not-needed-kamiwaza", (
            f"UAC-11 §5 R2: client api_key must be the runtime-lib placeholder "
            f"when no incoming Authorization is present (got {api_key!r}). "
            f"User-token passthrough to providers is explicitly disallowed "
            f"by design §4.4.5."
        )

    @pytest.mark.asyncio
    async def test_workroom_and_request_id_survive_when_authorization_is_present(
        self, monkeypatch
    ):
        """Even when the incoming request *does* carry ``Authorization`` (the
        bearer-pinning path used by the gateway), the workroom + request-id
        identity headers must still arrive at the dispatch client. These are
        the load-bearing fields for ENG-3822 attribution."""
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "https://kamiwaza.test/api/v1")

        request = MagicMock()
        request.headers = {
            **CANONICAL_ENVELOPE,
            "authorization": "Bearer platform-attested-bearer",
        }

        client = await get_model_client(request)
        forwarded = getattr(client, "default_headers", None) or getattr(
            client, "_default_headers", {}
        )
        forwarded_lower = {k.lower(): v for k, v in forwarded.items()}

        # The two fields ENG-3822 verifies in the audit event:
        assert forwarded_lower.get("x-user-id") == CANONICAL_ENVELOPE["x-user-id"]
        assert (
            forwarded_lower.get("x-workroom-id") == CANONICAL_ENVELOPE["x-workroom-id"]
        )
        # And the correlation id the dispatch boundary uses to join
        # request → audit event:
        assert forwarded_lower.get("x-request-id") == CANONICAL_ENVELOPE["x-request-id"]

    def test_forward_header_set_covers_uac11_relaxation_inputs(self):
        """The runtime-lib's allow-list must include every header the
        dispatch boundary will look for. Drift here silently breaks UAC-11
        attribution at the next platform release."""
        for key in CANONICAL_ENVELOPE:
            assert key in _FORWARD_HEADERS, (
                f"UAC-11 §5 R2: canonical envelope header {key!r} is not in "
                f"kamiwaza_extensions_lib.auth._FORWARD_HEADERS. The runtime "
                f"lib will drop it before the dispatch boundary sees it."
            )
