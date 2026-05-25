# Integration tests

Live tests that require a running Kamiwaza deployment. Gated by markers so
contributor PRs without a live cluster don't see false reds.

## Environment

| Env var | Purpose | Default |
|---|---|---|
| `KAMIWAZA_BASE_URL` | Primary cluster base URL (must end with `/api`) | `https://kamiwaza.test/api` |
| `KAMIWAZA_API_KEY` | API key for the primary cluster | unset |
| `KAMIWAZA_USERNAME` | Username for password-auth fallback | `admin` |
| `KAMIWAZA_PASSWORD` | Password for password-auth fallback | unset (falls back to kz-login) |
| `KAMIWAZA_VERIFY_SSL` | Set `false` for self-signed certs in dev | `true` |
| `KAMIWAZA_PEER_BASE_URL` | Federation peer cluster base URL (ENG-5784) | unset |
| `KAMIWAZA_PEER_API_KEY` | API key on the peer cluster (ENG-5784) | unset |

### Brokering env vars (capabilities probe + federated job tests)

The capabilities-probe-via-mesh and federated-job-audit-actor tests
require brokering to be active on both clusters. Brokering needs
Keycloak issuer URLs + cluster IDs configured on both sides; the
`kamiwaza-smoke.py federation-pair` script emits
``WARN: brokering not active on either side (KAMIWAZA_KC_* env vars
not set)`` when this is missing.

These two tests will fail with mesh-proxy errors (capabilities) and
job-result-marker errors (audit-actor) on fleet rigs that don't have
brokering wired up. The other four tests (pair, brokered-user-allowlist,
retrieval, unpair) work without brokering.

Full brokering setup is outside the scope of this harness — operators
running this suite against a fleet rig should ensure brokering is
active per the federation-pair runbook before relying on the
mesh-routing tests.

The peer-cluster env vars only activate the two-cluster federation tests
marked `@pytest.mark.requires_two_clusters`. When unset, those tests are
auto-deselected — contributor PRs without peer creds see no false reds.

## Running

```bash
# All live tests against one cluster
make test-live

# Two-cluster federation walkthrough (requires both clusters reachable)
KAMIWAZA_BASE_URL=https://lyra.example/api \
KAMIWAZA_API_KEY=... \
KAMIWAZA_PEER_BASE_URL=https://orion.example/api \
KAMIWAZA_PEER_API_KEY=... \
  uv run pytest -m "requires_two_clusters" tests/integration/test_federation_two_cluster_live.py -v
```

## Marker reference

| Marker | What it covers | Skip behavior |
|---|---|---|
| `live` | Tests that talk to a running Kamiwaza deployment | always selected when running `-m live` |
| `requires_embedding_model` | Live tests that need an active platform embedding deployment | auto-provisioned by `embedding_model_prerequisite` fixture; skipped if provisioning fails |
| `requires_two_clusters` | Live tests that need a federation peer cluster (ENG-5784) | auto-deselected at collection when `KAMIWAZA_PEER_BASE_URL` is unset; skipped at run time with an explicit reason when peer URL is set but `KAMIWAZA_PEER_API_KEY` is missing (partial-creds case) |

## Adding a federation-aware integration test

1. Add the marker:

   ```python
   pytestmark = [pytest.mark.live, pytest.mark.requires_two_clusters]
   ```

2. Depend on the peer-cluster fixtures from `tests/integration/conftest.py`:

   - `live_kamiwaza_client` — the primary cluster's `KamiwazaClient`
   - `live_kamiwaza_peer_client` — the peer cluster's `KamiwazaClient`

3. Keep teardown best-effort — federation state survives test failures
   and the next run gets a fresh per-run unique federation name.

See `test_federation_two_cluster_live.py` for the canonical walkthrough
(pair → brokered user → federated job → retrieval smoke → unpair).
