# Federation 1.2.0 UAT coverage matrix

This matrix maps the internal federation UAT to durable automation. It is the
SDK-side companion to
`docs/platform/federation/shared-idp-uat-playbook.md` in the internal
engineering documentation repository.

The required shared-IDP edge remains the fast compatibility gate. The
delegated-workload edge is separate because it requires the default-off RayJob
boundary, receiver-owned capability authority, and private package repository.

| UAT behavior | Durable coverage | Classification |
|---|---|---|
| Install prerequisites and supported topology | Kajiya install gate and the internal playbook preflight | Provisioning automation plus manual operator check |
| Shared issuer trust and `shared_idp` pairing | `test_federation_identity_gate_live.py`, `test_federation_shared_idp_gated_retrieval_live.py` | Automated SDK live |
| Receiver request, approval, onboarding, revoke, and distinct identities | `test_federation_request_approve_live.py`, `test_federation_user_onboarding_live.py`, `test_federation_onboarding_clearance_gate_live.py` | Automated SDK live |
| Exact receiver dataset discovery and retrieval | `test_required_mesh_dataset_list_returns_only_authorized_fixture`; `test_required_mesh_retrieval_returns_exact_post_gate_rows` | Required SDK live edge |
| Unonboarded-user denial | `test_unonboarded_shared_idp_user_rejected_by_receiver_allowlist` | Required SDK live edge |
| Receiver execution and mesh provenance | `test_required_mesh_job_reaches_receiver_and_returns_marker` | Required SDK live edge |
| Exact model discovery and chat | ENG-10429 immutable-image provider-parity UAT | Manual until the owned lane provisions a chat model |
| Isolated delegated RayJob with approved exact Python package versions | `test_shared_idp_delegated_job_installs_approved_package` | Required delegated-workload SDK edge |
| Private PyPI-compatible repository and arbitrary-internet denial | Internal playbook network probes; Deploy package-boundary contracts; `gate_packages/test_lifecycle.py` documents the manual probes | Manual live UAT plus automated Deploy contract |
| Job cancellation and durable grant revocation | ENG-10429 cancellation UAT and Core delegated-job lifecycle tests | Manual live UAT plus automated Core contract |
| Mid-job dataset, model, onboarding, user, and federation revocation | ENG-10429 lifecycle matrix and Core authority tests | Manual live UAT plus automated Core contract |
| Credential-agent restart, remint, AuthZ, and replay-store recovery | ENG-10429 outage UAT and Core agent/authority tests | Manual live UAT plus automated Core contract |
| Terminal-state denial and source-scoped result | ENG-10429 terminal UAT and Core result/lifecycle tests | Manual live UAT plus automated Core contract |
| PSK rotation, CA refresh, disconnect, and reconnect | `test_federation_trust_lifecycle_live.py`, `test_federation_idp_lifecycle_live.py`, `test_federation_two_cluster_live.py` | Automated SDK live |
| `peer_kc` default-off compatibility | `test_new_peer_kc_federation_refused_when_untrusted_disabled` | Automated SDK live |
| `receiver_realm` lifecycle | `test_federation_receiver_realm_live.py` | Automated SDK live |
| Console dialogs, trust labels, and verification cues | `managing-federation-trust-and-access.md` walkthrough | Manual UI UAT |
| Admission drain and full delegated-runtime rollback | ENG-10429 rollback UAT and Core/Deploy flag contracts | Manual live UAT plus automated contract |

## Required lanes

Run the six-case shared-IDP edge with its existing strict option:

```bash
uv run pytest \
  tests/integration/test_federation_shared_idp_gated_retrieval_live.py \
  --require-federation-edge \
  --live-peer-base-url "$KAMIWAZA_PEER_BASE_URL"
```

Run the isolated delegated-workload edge only after the receiver has enabled the
release 1.2.0 delegated-job contract and configured its private package catalog:

```bash
export KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON='["humanize==4.13.0", "kamiwaza-sdk==1.1.0"]'
export KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON='["humanize", "kamiwaza_sdk"]'

uv run pytest \
  tests/integration/test_federation_delegated_workload_live.py \
  --require-delegated-workload-edge \
  --live-peer-base-url "$KAMIWAZA_PEER_BASE_URL"
```

Both strict options reject missing cases and promote every selected skip to a
failure. The package coordinate is part of the request, and the live result
must report the exact installed distribution version. Repository location, CA
trust, and credentials remain receiver-owned installation state. The
playbook's network probes separately prove that the private repository is
reachable and arbitrary internet egress is denied; those probes are manual.

## Manual-only remainder

The rows that mutate a running workload or deliberately fail an authority
dependency remain manual because the shared nightly pair does not yet expose a
safe fault-injection control. The UI row remains manual because the console is
an operator workflow rather than an API contract. These rows have explicit
owners instead of being hidden in exploratory scripts.
