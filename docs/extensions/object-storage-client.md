# Object-storage client

`kamiwaza_extensions.client` is the canonical SDK client for S3-compatible
object storage and is used by `kz-ext publish` for broker-backed catalog access.
It provides complete client, resource, credential-provider, and multi-bucket
behavior under a provider-neutral namespace.

The storage API is boto3-compatible. Cloudflare R2 and its credential broker
are supported capabilities, not assumptions imposed on every catalog.

## Public API

```python
from kamiwaza_extensions.client import CredentialProvider, get_client, get_resource

# Automatic resolution: explicit credentials, AWS profile, AWS/R2 environment,
# then broker-backed SSO.
s3 = get_client(auth_mode="auto")

# Deterministic broker authentication for one bucket.
catalog = get_client(bucket="extensions-dev", auth_mode="sso")

# The boto3 resource API remains available.
resource = get_resource(auth_mode="static")

# Raw credentials remain available for other adapters.
credentials = CredentialProvider(
    auth_mode="sso", bucket="extensions-dev"
).get_credentials()
```

With `auth_mode="sso"` and no bucket, `get_client()` retains the lazy
multi-bucket proxy. It requests and refreshes credentials independently for
each bucket. `kz-ext publish` intentionally uses the single-bucket form because
one publish profile owns one catalog bucket.

## Publish profiles

Credential specifications are explicit so ambient developer credentials do
not silently redirect a publish:

| Specification | Behavior |
|---|---|
| `env` | Existing boto3 environment/session chain |
| `aws-profile:<name>` | Existing named boto3 profile |
| `sso` or `client:sso` | Forced broker-backed SSO for the profile bucket |
| `client:static` | Canonical static chain; fails if credentials are absent |
| `client:auto` | Automatic static resolution followed by broker-backed SSO |

```sh
kz-ext config publish-profile dev \
  --registry ghcr.io/my-org \
  --catalog-endpoint https://ACCOUNT.r2.cloudflarestorage.com \
  --catalog-bucket extensions-dev \
  --catalog-credentials sso
```

`R2_BROKER_URL` configures the broker. If a valid token is not cached, the CLI
prints a login URL and waits for a loopback callback. Set
`R2_AUTH_NON_INTERACTIVE=true` in CI so missing credentials fail immediately.
`R2_AUTH_CALLBACK_TIMEOUT_SECONDS` controls the interactive callback timeout.

The `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT_URL`,
`R2_ACCOUNT_ID`, `AWS_*`, and `KAMIWAZA_REGISTRY_*` inputs remain supported by
the reusable client. Existing `kz-ext` profiles using `env` or
`aws-profile:<name>` are unchanged.
