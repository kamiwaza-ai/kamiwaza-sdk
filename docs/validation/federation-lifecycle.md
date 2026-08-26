# Run the owned shared-IdP federation lifecycle

`kamiwaza-validate-federation` is the SDK-owned, runtime-neutral command path
for the shared-IdP federation scenario. Kajiya invokes the same provider
phases, but a pair of existing clusters can be qualified directly with these
JSON contracts.

The provider executes the exact nine registered cases for each selected
mesh-edge: three clearance retrievals, three tenant-negative retrievals,
authorized dataset listing, a receiver job marker, and an unonboarded-user
denial. Required cases fail; they are never silently skipped.

## Inputs

Create a `profile.json` describing both existing clusters and their edge:

```json
{
  "schema": "kamiwaza.validation-profile/v1",
  "deployment": {
    "provider": "existing-host",
    "topology_id": "two-cluster-existing",
    "ephemeral": false
  },
  "clusters": [
    {
      "id": "cluster-a",
      "roles": ["controller"],
      "node_count": 1,
      "hardware": {"accelerators": []},
      "features": {"rebac": true}
    },
    {
      "id": "cluster-b",
      "roles": ["controller"],
      "node_count": 1,
      "hardware": {"accelerators": []},
      "features": {"rebac": true}
    }
  ],
  "mesh": {
    "edges": [
      {"initiator": "cluster-a", "receiver": "cluster-b", "identity_mode": "shared_idp"}
    ]
  },
  "validation": {
    "level": "smoke",
    "fixture_mode": "owned",
    "include": ["sdk.federation.shared-idp/v1"],
    "exclude": []
  },
  "inference_targets": []
}
```

Create a `runtime.json` with both API and Kubernetes references. References
must point to files; secret values are never accepted inline or persisted in
provider artifacts:

```json
{
  "schema": "kamiwaza.runtime-context/v1",
  "run_id": "manual-federation-001",
  "ownership_key_ref": "file:///absolute/path/manual-federation-001.key",
  "secret_refs": {
    "shared-idp-admin-password": "file:///absolute/path/idp-admin.password",
    "shared-idp-persona-password": "file:///absolute/path/persona.password"
  },
  "clusters": [
    {
      "id": "cluster-a",
      "base_url": "https://cluster-a.example/api",
      "api_key_ref": "file:///absolute/path/cluster-a.pat",
      "kubeconfig_ref": "file:///absolute/path/cluster-a.kubeconfig"
    },
    {
      "id": "cluster-b",
      "base_url": "https://cluster-b.example/api",
      "api_key_ref": "file:///absolute/path/cluster-b.pat",
      "kubeconfig_ref": "file:///absolute/path/cluster-b.kubeconfig"
    }
  ]
}
```

Set the public Keycloak origin and, when a gate package is not already
installed on the receiver, its immutable package index and digest:

```bash
export KAMIWAZA_SHARED_IDP_PUBLIC_URL=https://idp.example
export KAMIWAZA_SHARED_IDP_ADMIN_URL=https://idp.example
export KAMIWAZA_SHARED_IDP_ADMIN_USER=admin
export KAMIWAZA_FEDERATION_GATE_INDEX_URL=https://packages.example/simple
export KAMIWAZA_FEDERATION_GATE_HASH=sha256:<gate-package-digest>
```

The ownership key should be unique per run and mode `0600`. Keep it until
teardown has passed.

## Execute and clean up

```bash
kamiwaza-validate-federation describe --json

kamiwaza-validate-federation resolve \
  --profile profile.json \
  --plan plan.json

kamiwaza-validate-federation prepare \
  --plan plan.json \
  --runtime runtime.json \
  --state state.json

kamiwaza-validate-federation run \
  --plan plan.json \
  --runtime runtime.json \
  --state state.json \
  --evidence evidence.json

kamiwaza-validate-federation teardown \
  --runtime runtime.json \
  --state state.json \
  --evidence cleanup.json
```

Always run `teardown`, including after a failed prepare or run. The provider
records authenticated state before each owned mutation and removes resources
in reverse order. `state.json`, `evidence.json`, and `cleanup.json` contain
digests, IDs, and outcomes—not PATs, passwords, or kubeconfig contents.
