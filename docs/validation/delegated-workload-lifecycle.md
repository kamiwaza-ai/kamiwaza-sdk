# Delegated-workload scenario provider

`kamiwaza-validate-delegated-workload` is the SDK-owned provider for the
strict delegated RayJob boundary.  It uses the same JSON-file lifecycle as the
shared-IdP federation provider (`describe`, `resolve`, `prepare`, `run`, and
`teardown`) and delegates pairing, the shared realm, and ownership-guarded
cleanup to that provider's lifecycle implementation.

The scenario is comprehensive-only and resolves an edge only when its profile
declares both `identity_mode: shared_idp` and the stable edge capability
`federation/delegated-workload:v1`.  An explicit include fails closed when no
compatible edge is present; an ordinary comprehensive run leaves the scenario
not-applicable when the capability is absent.

Before `resolve`, configure the exact, public package fixture coordinates and
matching import names:

```shell
export KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON='["humanize==4.13.0", "kamiwaza-sdk==1.1.0"]'
export KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON='["humanize", "kamiwaza_sdk"]'
```

The provider accepts only normalized `name==version` coordinates, requires at
least two packages, and carries no repository URL or credential into the
plan.  The receiver's approved package catalog remains the source of truth.
The run case first proves that the base image does not already contain every
exact fixture, then submits a delegated job and verifies imports, versions,
the gate audit, and receiver provenance.

For a direct invocation, use the standard provider protocol:

```shell
kamiwaza-validate-delegated-workload describe --json
kamiwaza-validate-delegated-workload resolve --profile profile.json --plan plan.json
kamiwaza-validate-delegated-workload prepare --plan plan.json --runtime runtime.json --state state.json
kamiwaza-validate-delegated-workload run --plan plan.json --runtime runtime.json --state state.json --evidence evidence.json
kamiwaza-validate-delegated-workload teardown --runtime runtime.json --state state.json --evidence cleanup.json
```

The command is intentionally usable outside Kajiya; Kajiya's provider registry
adds it to the composed federation/inference lane, while topology capability
facts decide whether it resolves.
