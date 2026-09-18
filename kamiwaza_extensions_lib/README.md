# kamiwaza-extensions-lib

Runtime library for Kamiwaza extensions. Provides FastAPI auth middleware,
identity extraction, a typed Kamiwaza model client, and session management
for extension backends.

This package is intentionally separate from `kamiwaza-sdk`: extension
backends need a lightweight async library, not the full SDK with its sync
HTTP client and 20+ service modules.

## Install

```bash
pip install 'kamiwaza-extensions-lib>=0.6,<0.7'
```

## Usage

```python
from kamiwaza_extensions_lib import (
    KamiwazaExtClient,
    platform_request,
    require_auth,
    extract_identity,
)
```

## Calling platform APIs from an extension backend

Use `platform_request()` with the incoming FastAPI request and the exact,
canonical platform route. The path must be root-relative and include the
platform `/api` prefix; collection routes must include their canonical trailing
slash when the platform declares one:

```python
response = await platform_request(
    request,
    "GET",
    "/api/catalog/datasets/",
)
response.raise_for_status()
datasets = response.json()
```

The helper uses the container-routable platform URL, forwards the current
ForwardAuth envelope, and deliberately refuses to follow HTTP redirects. Set
the optional `timeout=` in seconds when the 30-second default is unsuitable.
Each call currently uses a short-lived client; connection pooling is tracked in
[GitHub issue #63](https://github.com/kamiwaza-ai/kamiwaza-sdk/issues/63). Do not
construct absolute platform URLs or use a raw `httpx.AsyncClient` for
request-bound platform calls. Caller headers cannot replace the envelope's
authentication, routing, framing, or `X-Request-Id` correlation fields.

User-bound clients ignore `HTTP_PROXY`, `HTTPS_PROXY`, `SSL_CERT_FILE`, and
`SSL_CERT_DIR`. For a private CA, mount its PEM bundle in the backend container
and set `KAMIWAZA_CA_BUNDLE` to that file path. Do not disable certificate
verification in production.

For untrusted content, set `max_response_bytes=` to a positive integer to limit
the returned body, including error responses. This option is unreleased; it
requires a runtime-library release containing the bounded-response change.
Bounded calls request identity encoding and reject compressed responses before
reading the body. They close the stream on overflow or cancellation. The limit
bounds the retained body, rather than total process memory or network buffers.
Calls without a limit keep their existing behavior.

The helper raises:

- `ValueError` for invalid paths, methods, headers, or caller overrides
- `MisboundAuthError` when the platform-provided ForwardAuth envelope is
  malformed or contains an ambiguous duplicate field
- `UnexpectedContextError` when `KAMIWAZA_API_URL` is missing or invalid, or a
  bounded response uses compressed encoding despite requesting identity encoding
- `PlatformRedirectError` (a specialized `UnexpectedContextError`) when the
  canonical route redirects
- `PlatformOutageError` for network, timeout, or upstream protocol failures
- `PlatformResponseTooLargeError` when a bounded response exceeds its byte limit

See `CHANGELOG.md` in this directory for release notes.
