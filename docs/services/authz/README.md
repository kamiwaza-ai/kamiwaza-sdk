# Authorization Service (ReBAC)

Client helpers for the relationship-based access control surface exposed under
`/auth/*` in the Kamiwaza API.  Methods live in
`kamiwaza_sdk/services/authz.py`.

## Usage

```python
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.authz import (
    CheckRequest,
    RelationshipTuple,
    SubjectModel,
    ObjectModel,
)

client = KamiwazaClient("https://localhost/api")

# Upsert a tuple (admin credentials required)
tuple_payload = RelationshipTuple(
    subject=SubjectModel(namespace="user", id="5ac2cafa-1fbd-4b66-aba1-40b6c023612c"),
    relation="owns",
    object=ObjectModel(namespace="dataset", id="urn:dataset:foo"),
)
client.authz.upsert_tuple(tuple_payload)

# Check access (resolves tenant from the authenticated token)
check = client.authz.check_access(
    CheckRequest(
        subject=tuple_payload.subject,
        relation="owns",
        object=tuple_payload.object,
    )
)
print(check.allow, check.decision_id)
```

Tuple delete helpers mirror the REST API:

- `client.authz.delete_tuple(...)`
- `client.authz.delete_object(...)`

Pass `tenant_id="__default__"` when you need to override the tenant that will
own the tuple; otherwise it is inferred from the caller's token.
