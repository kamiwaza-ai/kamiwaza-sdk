# Connectors

`client.connectors` covers two distinct audiences:

- **Instance administration** (admin-scoped, cluster-wide): register a connector
  type, create/update/delete a configured instance, subscribe an out-of-core
  connector.
- **Connector surfaces** (member-scoped, per workroom): discover which
  connectors the acting user may use, verify their connection, and browse,
  search, and fetch content from a connector's read surfaces.

The per-user OAuth ceremony that connects a member's own provider account is an
interactive platform flow and is deliberately not wrapped by the SDK. The
platform brokers provider tokens itself, so no method here accepts, returns, or
stores a provider credential.

## Instance administration

```python
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.connectors import ConnectorCreate

client = KamiwazaClient(base_url="https://kamiwaza.example/api")

client.connectors.list()             # every registered connector (admin view)
client.connectors.list_available()   # enabled connectors, user-safe metadata
client.connectors.get(connector_id)

client.connectors.create(
    ConnectorCreate(
        name="Microsoft 365",
        connector_type="m365",
        config={"tenant_id": "…", "client_id": "…"},
        scopes=["Files.Read.All"],
    )
)
```

`connector_type` is an open string resolved against the published catalog, and
`config` is opaque to the SDK — the platform validates it against the
connector's manifest. New connector types therefore need no SDK change.

See also `register_type` (publish a connector type to the cluster catalog),
`subscribe` (attach an out-of-core connector by manifest + endpoint), `update`,
and `delete`.

## Connector surfaces

Surface operations are scoped to a **workroom** and execute as the calling
member: the platform re-evaluates the user's provider ACLs on every request.
A connector absent from the surface catalog is not reachable through any other
method on this service — the catalog is the authoritative discovery call.

### Discovery

```python
catalog = client.connectors.list_surface_catalog(workroom_id)

for entry in catalog:
    entry.label            # "Microsoft 365"
    entry.connected        # did this member connect their own account?
    entry.ready_surfaces()      # surfaces registered and healthy in the workroom
    entry.searchable_surfaces() # …of those, the ones supporting full-text search
```

`list_connector_surfaces(workroom_id, connected_only=True)` is the same call
narrowed to instances the member has actually connected.

A surface is usable only when its `workroom_state` is `ready`; the
`ConnectorSurfaceCapability.ready` property encodes that, and anything else
(including `unknown`) is treated as unusable rather than optimistically tried.

### Verification

```python
result = client.connectors.verify_connection(connector_id)
if result.available:
    ...
```

`verify_connection` probes the provider under the member's stored credential and
reports per-capability results. **It is the one write on this surface**: the
platform persists the health it classifies, so a verify can move the member's
stored connection into a degraded or reauth-required state, and it issues live
third-party calls. Call it deliberately — on connect, on an explicit user
action, or after a surface call already failed — not on a timer and not fanned
out across a catalog. Gate on `available` — the fail-closed verdict
that requires both a usable `connection_status` and a passing check — rather
than reading `status` directly. `connection_usable` and `checks_passed` expose
the two halves separately when a caller needs to explain *why* access was
refused. An ambiguous result (unknown status, `partial` with no successful
check, a connection needing reauth) resolves to `available is False`.

### Browse and search

```python
from kamiwaza_sdk.schemas.connector_surfaces import (
    ConnectorBrowseRequest,
    ConnectorSearchRequest,
    ConnectorSurfaceRef,
)

# The workroom and connector instance always travel together.
ref = ConnectorSurfaceRef(workroom_id=workroom_id, connector_id=connector_id)

page = client.connectors.browse_surface(
    ref,
    ConnectorBrowseRequest(surface="files", container_id="folder-1", page_size=50),
)

page.items            # list[ConnectorNode]
page.next_page_token  # opaque; pass back as page_token for the next page
page.has_more

hits = client.connectors.search_surface(
    ref,
    ConnectorSearchRequest(surface="files", query="quarterly report"),
)
```

`page_size` is clamped into the platform's accepted 1–200 range rather than
rejected. Search is only accepted on surfaces that declare `search_supported`;
check `searchable_surfaces()` first instead of discovering it as a 501.

To walk several pages, `iter_surface_nodes` yields nodes across pages and is
bounded by `max_pages` (default 10) so a large provider surface cannot hang the
caller:

```python
for node in client.connectors.iter_surface_nodes(
    ref, ConnectorBrowseRequest(surface="files"), max_pages=3
):
    ...
```

### Content fetch

```python
from kamiwaza_sdk.schemas.connector_surfaces import ConnectorContentRequest

node = page.items[0]
content = client.connectors.fetch_surface_content(
    ref,
    node.id,
    ConnectorContentRequest.from_node(node),
)

if content.is_text:
    body = content.text(limit=100_000)
else:
    blob = content.content          # raw bytes, never silently decoded
```

`ConnectorContentRequest.from_node` copies the node's surface and its
`content_handle.query` provider locator, so callers never hand-assemble provider
identifiers. A `drive_id` or `mime_type` that arrives only inside the locator is
promoted into the declared parameter rather than dropped — connectors do ship
their export `mime_type` that way. An explicitly-set field always wins over a
locator entry of the same name, and `surface` is never accepted as a locator key.

Any *other* locator key is still sent, but the platform's content route declares
only `surface`, `drive_id` and `mime_type`, so an undeclared key is discarded
server-side today. Passing one is harmless and costs nothing if the route later
declares it — it is not a forward-compatibility guarantee.

Node ids are opaque and are percent-encoded on the way out. When the platform
sends a `Content-Disposition`, its filename wins over the caller's guess.

A missing content type counts as text, matching how the platform serves provider
payloads that declare no type. `is_partial` reports a 206 range response.

### Failure behavior

Surface methods let `APIError` propagate with its `status_code` intact instead
of collapsing failures into one exception, because callers must distinguish a
permanent denial from a transient outage:

| Status | Meaning |
| --- | --- |
| 400 | Invalid request, or a connector id the workroom does not resolve |
| 403 | Not permitted for this workroom / member |
| 404 | Connector or node not found |
| 409 | The member's connector connection is missing — send them to the catalog entry's `reauth` deep links, not a retry |
| 413 | Content exceeds the platform's size cap (permanent for that node) |
| 429 | Provider rate limit — transient, back off |
| 501 | Operation not supported for this surface |
| 502 | Upstream provider error |
| 503 | Connector not deployed; verification unavailable |

**401 is the exception, and it never reaches you as an `APIError`.** The client
intercepts every 401 before service code runs, refreshes the credential, retries
once, and then raises `AuthenticationError` — which descends from
`KamiwazaError`, *not* `APIError`, and carries no `status_code`. So this does
not fire:

```python
except APIError as exc:
    if exc.status_code == 401:   # unreachable on these methods
        ...
```

Catch `AuthenticationError` separately. Because the client retries before
raising, an expired credential also costs a duplicate content download.

### Timeouts

Every surface method takes a `timeout` override. Defaults are bounded: 15s for
the catalog (a local metadata read) and 30s for verification, browse, search,
and content fetch (all of which round-trip to a third-party provider).
