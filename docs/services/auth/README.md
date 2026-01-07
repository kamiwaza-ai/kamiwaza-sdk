# Authentication Service

The authentication helpers live in `kamiwaza_sdk/services/auth.py`.  They wrap the
Keycloak-backed auth gateway that fronts the Kamiwaza APIs.

> Base URL rule: set `base_url=https://<host>` (no `/auth` suffix). A quick preflight is `GET {base_url}/auth/ping` → 200. If base_url ends with `/auth`, SDK calls will double-prefix and fail.

## Getting Started

```python
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator

client = KamiwazaClient("https://localhost/api")

# Option 1 – Personal Access Token (recommended for automation)
# export KAMIWAZA_API_KEY=<pat>
# client automatically loads the token from the environment.

# Option 2 – username/password (interactive or CLI bootstrap)
client.authenticator = UserPasswordAuthenticator("admin", "kamiwaza", client.auth)

# Who am I?
me = client.auth.get_current_user()
print(me.username, me.roles)
```

> **Tip:** the SDK reads `KAMIWAZA_API_KEY` automatically.  For federated users,
> generate a PAT in the UI (or with `client.auth.create_pat`) and drop it in the
> environment before instantiating the client.

> **Note:** `/auth/users/me` depends on Keycloak token introspection.  In lite
> environments without Keycloak configured it may respond with 401; the token is
> still usable with ForwardAuth and PAT endpoints in that mode.

## Tokens & Logout

```python
# Perform a password grant and capture the refresh token
token = client.auth.login_with_password("admin", "kamiwaza")

# Explicit refresh using the refresh token value
new_token = client.auth.refresh_access_token(token.refresh_token)

# Coordinated logout clears cookies and returns front-channel redirect metadata
logout = client.auth.logout()
print(logout.front_channel_logout_url)
```

`UserPasswordAuthenticator` handles this dance automatically: it refreshes when the
token is close to expiring, keeps the refresh token, and falls back to a password
grant if the refresh is rejected.

## Personal Access Tokens

```python
from kamiwaza_sdk.schemas.auth import PATCreate

# Create a PAT – token only appears once in the response
pat = client.auth.create_pat(PATCreate(name="local-cli", ttl_seconds=86400))
print(pat.token)

# List and revoke
pat_list = client.auth.list_pats()
client.auth.revoke_pat(pat_list.pats[0].jti)
```

Use PATs for long-lived automation.  When a PAT is set in `KAMIWAZA_API_KEY`, the
client automatically authenticates without any extra code.

## ForwardAuth Headers

Some workloads (e.g. AppGarden apps) rely on Traefik’s ForwardAuth instead of the
first‑party FastAPI stack.  The SDK can call `/auth/validate` to fetch the header
bundle that needs to be proxied.

```python
headers = client.auth.forward_auth_headers()
print(headers.user_id, headers.user_roles)
print(headers.signature, headers.signature_ts)
```

A helper also surfaces the JWKS document for verifying PATs locally:

```python
jwks = client.auth.get_jwks()
print(jwks.keys[0].kid)
```

## Identity Providers & Local Users

Administrative helpers for IdP lifecycle, session purges, and local-user
management mirror the REST surface:

```python
from kamiwaza_sdk.schemas.auth import (
    RegisterIdPRequest,
    LocalUserCreateRequest,
    SessionPurgeRequest,
)

client.auth.list_identity_providers()
client.auth.purge_sessions(SessionPurgeRequest(
    tenant_id="__default__",
    subject_id="5ac2cafa-1fbd-4b66-aba1-40b6c023612c",
))

client.auth.create_local_user(LocalUserCreateRequest(
    username="new-admin",
    email="new-admin@example.com",
    password="changeme!",
    roles=["admin"],
))
```

These calls require an authenticated admin role and are intended for operational
tooling rather than end-user flows. In Auth-on deployments, `create_local_user`
provisions the Keycloak identity (transactional, rolls back on Keycloak failure),
and `reset_user_password` updates Keycloak only (Keycloak is authoritative for
login). In Auth-off mode, these calls remain DB-only.

