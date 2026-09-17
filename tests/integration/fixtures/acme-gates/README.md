# Gate test fixture

Version 1.2.0 exports `acme_gates.mini_access_tier_gate.MiniAccessTierGate`.
The provisioner publishes this wheel alongside the 1.0.0 and 1.0.1 artifacts
used by package lifecycle tests. Fresh federation setup selects 1.2.0.

A compatible package left by an interrupted run is reused. An installed
`acme-gates` package without the current classpath is rejected before installation;
changing the requested version cannot replace a package registered under the same
name. No automatic replacement, uninstallation, or dataset unbinding occurs.

On an isolated test cluster, remove the old fixture dataset bindings, uninstall
the obsolete gate package, and rerun fixture provisioning and setup. Do not apply
this cleanup to unrelated datasets or shared production packages.
