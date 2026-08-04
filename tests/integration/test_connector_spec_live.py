"""Connector-spec engine smoke — control plane (ENG-6952).

skip-not-fail: the whole module skips where the ``register-from-spec`` route is
absent (e.g. a develop baseline without the connector-spec engine), so the suite
stays green there and *exercises* on a feature-branch build that ships the
engine. kajiya runs ``pytest tests/integration`` with no ``-m`` filter, so these
collect everywhere and self-skip.

Scope: the deterministic control plane — register-from-spec happy path, spec
validation (inline-secret / missing-platform / extra-field rejections), and
publisher grant/revoke. The *data-plane* gated round-trip (register a Kamiwaza
self-connector and materialize gated records) needs the engine's
``allow_private_hosts`` toggle plus a reachable source, so it is built and
validated live in the M6 demo (ENG-6965), not here.

Gate requirement (ENG-6970): register-from-spec binds an ``AttributeGate`` by
classpath and validates it imports and is the right kind (design OQ-4). A stock
engine ships NO concrete ``AttributeGate`` — ``default_gates.py`` carries only
``AllowAllExecutionGate`` — because v1 gates are delivered as sanctioned
gate-packages (``kamiwaza_extensions.*``), not bundled in core. So the gated
happy-path register only runs when a real gate-package is installed: set
``KAMIWAZA_CS_SMOKE_GATE_CLASSPATH`` to its classpath to exercise it; absent
that, the happy-path skips (the full gated round-trip is validated in the M6
demo, which ships its own gate-package). The spec-validation rejections do NOT
need an importable gate — they fail at spec validation before gate binding.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from pydantic import SecretStr

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.catalog import SecretCreate

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# A real, importable gate-package classpath supplied by the operator/CI. None on
# a stock engine (which ships no concrete AttributeGate, design OQ-4), in which
# case the gated happy-path register skips.
_REAL_GATE_CLASSPATH = os.environ.get("KAMIWAZA_CS_SMOKE_GATE_CLASSPATH")
# Placeholder used ONLY by the spec-validation rejection specs: those are
# rejected at spec validation before gate binding, so this is never imported
# and never used to register a standing dataset.
_PLACEHOLDER_GATE_CLASSPATH = "kamiwaza_extensions.cs_smoke.PlaceholderAttributeGate"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _resolve_owner(client) -> str:
    try:
        profile = client.get("/auth/users/me")
    except Exception:  # pragma: no cover - live guard
        profile = {}
    username = (profile.get("username") or "sdk-integration").replace("@", "-")
    return profile.get("urn") or f"urn:li:corpuser:{username}"


def _engine_present(client) -> bool:
    """True when the POST register-from-spec route exists.

    A baseline without the engine answers the POST with 404 (no path) or 405:
    the path is shadowed by ``GET /catalog/datasets/{urn}``, so POST is
    Method-Not-Allowed. A present engine validates the empty spec and returns
    400/422. Treat 404 and 405 as 'engine absent' so the module skips (not
    fails) on a develop baseline, and only exercises on a feature-branch build.

    A 401/403 (auth/RBAC) is deliberately treated as 'present' — an auth
    misconfiguration on a host that ships the engine is worth surfacing
    downstream, not masking as a clean skip.
    """
    try:
        client.catalog.register_from_spec({})
    except APIError as exc:
        return getattr(exc, "status_code", None) not in (404, 405)
    return True  # an empty spec was accepted? the route certainly exists


@pytest.fixture(scope="module")
def cs_engine(live_kamiwaza_session_client):
    if not _engine_present(live_kamiwaza_session_client):
        pytest.skip(
            "connector-spec engine not present on this deployment "
            "(register-from-spec -> 404/405); feature-branch build not installed"
        )
    return live_kamiwaza_session_client


@pytest.fixture
def brokered_secret(cs_engine):
    """Mint a catalog secret and yield its URN as a valid credential_ref."""
    client = cs_engine
    owner = _resolve_owner(client)
    payload = SecretCreate(
        name=_unique("cs-smoke-secret"),
        value=SecretStr("integration-bearer-token"),
        owner=owner,
    )
    secret_urn = client.catalog.secrets.create(payload, clobber=True)
    try:
        yield secret_urn
    finally:
        try:
            client.catalog.secrets.delete(secret_urn)
        except APIError:
            pass


def _spec(
    index: str,
    credential_ref: str,
    *,
    gate_classpath: str = _PLACEHOLDER_GATE_CLASSPATH,
) -> dict:
    """A minimal valid connector spec; base_url is never fetched at register."""
    return {
        "platform": "kamiwaza",
        "base_url": "https://example.test/api",
        "endpoint": {"method": "GET", "path": "/v1/items", "items_path": "items"},
        "index": index,
        "pagination": {"max_pages": 1, "page_size": 50},
        "auth": {"kind": "bearer", "credential_ref": credential_ref},
        "data_attribute_fields": ["data_class"],
        "gate": {"type": gate_classpath, "config": {"data_class_field": "data_class"}},
    }


# --------------------------------------------------------------------------- #
# register-from-spec — happy path
# --------------------------------------------------------------------------- #
def test_register_from_spec_returns_dataset_urn(cs_engine, brokered_secret):
    if not _REAL_GATE_CLASSPATH:
        pytest.skip(
            "no importable AttributeGate configured: a stock engine ships none "
            "(design OQ-4 — v1 gates are sanctioned gate-packages). Set "
            "KAMIWAZA_CS_SMOKE_GATE_CLASSPATH to an installed gate-package "
            "classpath to exercise the gated register here; the full gated "
            "round-trip is validated in the M6 demo (ENG-6965)."
        )
    client = cs_engine
    index = _unique("cs-smoke")
    dataset_urn: str | None = None
    try:
        dataset_urn = client.catalog.register_from_spec(
            _spec(index, brokered_secret, gate_classpath=_REAL_GATE_CLASSPATH)
        )
        assert dataset_urn and dataset_urn.startswith("urn:li:dataset:")
        # The standing dataset is queryable through the catalog.
        fetched = client.catalog.get_dataset(dataset_urn)
        assert fetched.urn == dataset_urn
    finally:
        if dataset_urn:
            try:
                client.catalog.datasets.delete(dataset_urn)
            except APIError:
                pass


# --------------------------------------------------------------------------- #
# register-from-spec — spec validation (engine is authoritative: extra=forbid)
# --------------------------------------------------------------------------- #
def test_register_rejects_inline_secret_value(cs_engine):
    """credential_ref must be a secret URN, never a raw secret value (INF-CB5)."""
    spec = _spec(_unique("cs-bad-inline"), credential_ref="raw-bearer-token-not-a-urn")
    with pytest.raises(APIError) as exc:
        cs_engine.catalog.register_from_spec(spec)
    status = exc.value.status_code
    assert status is not None and 400 <= status < 500


def test_register_rejects_missing_platform(cs_engine, brokered_secret):
    spec = _spec(_unique("cs-bad-platform"), brokered_secret)
    spec.pop("platform")
    with pytest.raises(APIError) as exc:
        cs_engine.catalog.register_from_spec(spec)
    status = exc.value.status_code
    assert status is not None and 400 <= status < 500


def test_register_rejects_unknown_field(cs_engine, brokered_secret):
    """The drift guard: a producer cannot smuggle a field the engine ignores."""
    spec = _spec(_unique("cs-bad-extra"), brokered_secret)
    spec["totally_unknown_field"] = "drift"
    with pytest.raises(APIError) as exc:
        cs_engine.catalog.register_from_spec(spec)
    status = exc.value.status_code
    assert status is not None and 400 <= status < 500


# --------------------------------------------------------------------------- #
# publisher grant / revoke (admin-gated, native-realm)
# --------------------------------------------------------------------------- #
def test_publisher_grant_then_revoke(cs_engine):
    client = cs_engine
    subject = f"urn:li:corpuser:cs-smoke-{uuid4().hex[:8]}"
    # Admin grant + revoke should both succeed (204, no exception). Revoke is
    # idempotent-friendly; the grant is cleaned up by the revoke itself.
    client.catalog.add_publisher(subject)
    client.catalog.remove_publisher(subject)
