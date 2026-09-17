# Gate test fixture

Version 1.2.0 exports `acme_gates.mini_access_tier_gate.MiniAccessTierGate`.
The provisioner publishes this wheel alongside the 1.0.0 and 1.0.1 artifacts
used by package lifecycle tests. Fresh federation setup selects 1.2.0.

This is an unpublished SDK test fixture, not a production gate or an installed
SDK dependency. Use an isolated test cluster with the gate-package runtime and
its writable package PVC. The [fixture provisioner](../../_gate_fixture.py)
describes building the wheel once, serving those same bytes on the receiver,
and exporting `M5_TEST_WHEEL_DIR` and `M5_TEST_INDEX_URL` for integration tests.
Run its `provision`, `env`, and eventual `teardown` commands only against the
intended test cluster. Provisioning stages the wheel and dataset; the test
setup installs and binds the package.

The gate requires the caller's `access_tier` attribute through
`x-user-access-tier` and dataset rows with `tier` values `PUBLIC`, `PRIVATE`, or
`CONFIDENTIAL` (or a configured `tier_field`). These are fixture-specific
contracts; a same-named package from another source is not interchangeable.

A package left by an interrupted run is reused only when its package spec,
version, SHA-256 digest, active status, and classpath match the expected SDK
artifact. Integration setup derives the digest from the local wheel;
[validation lifecycle setup](../../../../docs/validation/federation-lifecycle.md)
requires `KAMIWAZA_FEDERATION_GATE_HASH` even for reuse and an index for fresh
installation. Missing or mismatched metadata is rejected before reuse;
changing the requested version cannot replace a package registered under the same
name. No automatic replacement, uninstallation, or dataset unbinding occurs.

On an isolated test cluster, remove the old fixture dataset bindings, uninstall
the obsolete gate package, and rerun fixture provisioning and setup. Do not apply
this cleanup to unrelated datasets or shared production packages.
