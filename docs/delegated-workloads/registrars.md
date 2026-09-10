# Delegated workload registrars

Registrars are trusted platform integration components. They translate an
operator-authorized deployment or protected-resource definition into an
immutable Core registration. They are not part of an application workload or
resource-server request path, and their credentials must never be mounted into
either process.

Use two independent registrar authorities:

- a workload registrar creates clients, immutable revisions, and separate
  control-plane and executor roles; and
- a resource registrar creates immutable protected-resource descriptors and
  activates their lifecycle revisions.

The public SDK exposes `WorkloadRegistrationAdapter` and
`ResourceRegistrationAdapter` as neutral ports for these components. The SDK
does not grant registrar authority or provide a client-side self-registration
shortcut.

## Workload registration

Register a stable workload client before installing a runnable revision. A
revision binds all authority-sensitive deployment inputs, including:

- tenant, client, owner, and immutable revision identity;
- runtime artifact and configuration digests;
- the authority manifest and supported contract versions;
- separate control-plane and executor roles with closed platform operations;
- attestation selectors and ordered acceptable profile sets; and
- workload proof-key and lifecycle state.

Keep role boundaries real. The control-plane role may reserve idempotent run
occurrences. The executor role may attest, claim opaque run references, use
fenced run authority, reserve exact effects, and report lifecycle outcomes.
Neither role may register itself, approve member consent, mint its own
capabilities, or act as the other role merely because both are deployed by the
same product.

An adapter implements this structural port:

```python
from collections.abc import Mapping

from kamiwaza_sdk.delegated_workloads import WorkloadRegistrationAdapter


class OperatorWorkloadRegistrar:
    @property
    def adapter_id(self) -> str:
        return "example-operator-workloads:v1"

    async def reconcile_workload(
        self,
        deployment: Mapping[str, object],
    ) -> Mapping[str, object]:
        authorized = validate_operator_owned_deployment(deployment)
        return await platform_registration_api.reconcile_workload(authorized)


adapter: WorkloadRegistrationAdapter = OperatorWorkloadRegistrar()
```

`validate_operator_owned_deployment` and `platform_registration_api` are
platform-owned seams, not SDK helpers. The adapter must derive its input from
trusted installation state. Never accept a tenant, owner, selector, role,
operation, digest, key, or lifecycle state from the workload being registered.

## Resource registration

A resource descriptor is separate from workload registration. It fixes:

- HTTPS audience origins and canonical resource-ID syntax;
- versioned actions, accepted methods, and request-digest rules;
- `read/none` or `mutation/exact_it_approval` classification;
- policy and quota adapter identifiers plus quota dimensions;
- optional closed broker-operation types and safe-result normalization;
- resource-guard version and explicit compatibility; and
- staged, active, retired, or revoked lifecycle state.

The resource server may ship a staged descriptor as installation input, as the
neutral document example does. Only the authorized registrar validates and
activates it. The server receives the resulting immutable revision ID as
non-secret configuration; it does not receive registrar credentials.

```python
from collections.abc import Mapping

from kamiwaza_sdk.delegated_workloads import ResourceRegistrationAdapter


class PlatformResourceRegistrar:
    @property
    def adapter_id(self) -> str:
        return "platform-resources:v1"

    async def reconcile_resource(
        self,
        descriptor: Mapping[str, object],
    ) -> Mapping[str, object]:
        authorized = validate_platform_resource_descriptor(descriptor)
        return await platform_registration_api.reconcile_resource(authorized)


adapter: ResourceRegistrationAdapter = PlatformResourceRegistrar()
```

An adapter is not authorization by itself. Its caller must authenticate as the
correct registrar class for the target tenant, and Core repeats schema,
authority, lifecycle, immutability, and downgrade checks transactionally.

## Revisions, activation, and rollback

Treat every authority-sensitive change as a new revision. This includes a new
audience, role, selector, action, method, canonicalizer, approval class,
adapter, guard version, compatibility rule, runtime digest, or authority
manifest.

Identical reconciliation is idempotent. Reusing a version with changed inputs
is a conflict. Activation is explicit, and only one revision of a resource or
workload client may receive new authority at a time. A prior workload revision
may drain claims that were already issued within its registered bound; it may
not receive new claims.

Rollback activates a previously registered compatible revision through the
same trusted path. It never revives a revoked key, claim, capability, grant, or
registration. If rollback widens authority relative to current member consent,
obtain fresh consent first.

## Fail-closed rules

A registrar must reject all of these before registration:

- caller-controlled tenant, owner, subject, role, selector, or lifecycle data;
- an unversioned adapter, profile, protocol, descriptor, or guard identifier;
- duplicate roles, methods, audiences, keys, or compatibility entries;
- non-HTTPS resource audiences or unbounded resource identifiers;
- a mutation without exact IT approval, or a read that asks for mutation
  authority;
- a role operation, action, broker type, or quota dimension outside the
  registered vocabulary; and
- a request to overwrite an immutable version or silently broaden a default.

Return only safe registration status, revision identifiers, descriptor
digests, compatibility, lifecycle, and correlation data. Do not return proof
private keys, projected assertions, registrar credentials, member sessions,
provider credentials, or raw installation secrets.

## Operational separation

Run registrars in the platform installation or operator trust boundary. Scope
their credentials to the registration class and tenant they manage. Do not
place them in a general job worker, resource pod, CI artifact, browser, queue,
or application database.

Audit every create, reconcile, activation, retirement, revocation, and denied
downgrade with the authenticated registrar actor and immutable target digest.
Registration availability may delay rollout, but it must not make Core accept
an unregistered workload or resource.

Continue with the [quickstart](quickstart.md) for a client/resource walkthrough
and [protected-resource guide](resources.md) for adapter and guard authoring.
