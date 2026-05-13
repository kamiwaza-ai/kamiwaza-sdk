"""T5.18-full / ENG-4746 — WS-M3 demo end-to-end SDK walkthrough.

Live integration test: exercises the full 8-step M3 demo flow against
a real paired LYRA + ORION cluster, using only customer-facing SDK
calls. **This is the M3 ship gate** — when this test passes against
the fleet (spark-2 + evo-x2-2 as the paired pair), M3 is shippable.

Extends T5.18-skeleton (WS-M1, federation-only) by adding the M3
ergonomics layers: subject upsert via SDK, cluster execution-gate
binding via SDK, dataset creation + attribute-gate binding via SDK.
Mirrors the README's eight-step walkthrough exactly.

The test is marked ``@pytest.mark.live`` and gated on env vars — it
no-ops in standard CI and only runs when the operator points the SDK
at a real cluster:

    export KAMIWAZA_LYRA_URL=https://spark-2.kamiwaza.test
    export KAMIWAZA_LYRA_TOKEN=<lyra-admin-pat>
    export KAMIWAZA_ORION_URL=https://evo-x2-2.kamiwaza.test
    export KAMIWAZA_ORION_TOKEN=<orion-admin-pat>
    make test-live  # or: uv run pytest -m live tests/integration/test_m3_walkthrough_live.py

The test cleans up after itself: subject deletion + dataset deletion +
execution-gate clear + federation disconnect are invoked even on
failure (try/finally) so re-runs against the same fleet don't pile up
state.

Demo-gate assertion: the federated job's audit_actor round-trips back
as the originating user (``demo-baker@<lyra-cluster-uuid>``), not as
a system principal. That's the same load-bearing signal the WS-M1
skeleton uses, now riding on a fully-typed M3 setup.py path.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
]


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} env var not set; skipping M3 live walkthrough")
        raise AssertionError("unreachable")  # mypy: pytest.skip raises Skipped
    return value


@pytest.fixture
def lyra_url() -> str:
    return _require_env("KAMIWAZA_LYRA_URL")


@pytest.fixture
def lyra_token() -> str:
    return _require_env("KAMIWAZA_LYRA_TOKEN")


@pytest.fixture
def orion_url() -> str:
    return _require_env("KAMIWAZA_ORION_URL")


@pytest.fixture
def orion_token() -> str:
    return _require_env("KAMIWAZA_ORION_TOKEN")


@pytest.fixture
def demo_username() -> Iterator[str]:
    """Per-run unique username so concurrent test runs don't collide."""
    name = f"m3-demo-{uuid.uuid4().hex[:8]}"
    yield name


@pytest.fixture
def demo_dataset_name() -> Iterator[str]:
    name = f"m3-demo-dataset-{uuid.uuid4().hex[:8]}"
    yield name


def test_m3_full_walkthrough_against_live_fleet(
    lyra_url: str,
    lyra_token: str,
    orion_url: str,
    orion_token: str,
    demo_username: str,
    demo_dataset_name: str,
) -> None:
    """End-to-end M3 demo through SDK only — the milestone ship gate.

    Steps mirror README §"Federation walkthrough" exactly:
      1. Pair LYRA → ORION
      2. Seed personas via kz.subjects.upsert
      3. Bind cluster execution gate via kz.cluster.set_execution_gate
      4. Register dataset via kz.datasets.create
      5. Bind dataset attribute gate via kz.datasets.set_gate
      6. Allowlist brokered user + ReBAC viewer grant
      7. Submit federated job via kz.jobs.run
      8. Assert audit_actor round-trip
    """
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.exceptions import KamiwazaError

    federation_id = None
    dataset_urn = None
    execution_gate_classpath = (
        "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
    )

    with KamiwazaClient(base_url=lyra_url, api_key=lyra_token) as lyra:
        try:
            # Step 1 — Pair LYRA with ORION.
            fed = lyra.federations.pair(
                name="ORION-m3-walkthrough",
                role="initiator",
                remote_url=orion_url,
                remote_admin_token=orion_token,
            )
            federation_id = fed.id
            assert fed.status == "PAIRED"

            # Step 2 — Seed persona via SDK (replaces v0.1.x KC recipe).
            subject = lyra.subjects.upsert(
                demo_username,
                attributes={
                    "clearance": "TS",
                    "country": "USA",
                    "programs": ["IRIS", "ARGOS"],
                },
                password="demo-pw",
            )
            assert subject.username == demo_username
            assert subject.attributes["clearance"] == "TS"
            assert subject.attributes["programs"] == ["IRIS", "ARGOS"]

            # Step 3 — Bind cluster execution gate (replaces kubectl-exec).
            exec_binding = lyra.cluster.set_execution_gate(
                type=execution_gate_classpath, config={}
            )
            assert exec_binding.kind == "execution"
            assert exec_binding.type == execution_gate_classpath

            # Step 4 — Register dataset.
            dataset = lyra.datasets.create(
                name=demo_dataset_name,
                platform="file",
                environment="PROD",
                properties={"path": f"/tmp/{demo_dataset_name}"},
            )
            dataset_urn = dataset.urn
            assert dataset.name == demo_dataset_name

            # Step 5 — Bind dataset's attribute gate. Skipped if no
            # extension gate is installed on the fleet; surface the
            # error rather than silently skip so M3 closeout can decide.
            try:
                ds_binding = lyra.datasets.set_gate(
                    dataset_urn,
                    type=(
                        "kamiwaza_extensions.classified_conjunction_gate."
                        "ClassifiedConjunctionGate"
                    ),
                    config={
                        "classification_field": "classification",
                        "releasable_to_field": "releasable_to",
                    },
                )
                assert ds_binding.kind == "attribute"
            except KamiwazaError as exc:
                if exc.status_code != 404:
                    raise
                pytest.skip(
                    "ClassifiedConjunctionGate extension not installed on the "
                    "fleet; partial M3 walkthrough run. Install the gate "
                    "extension to exercise the full ship gate."
                )

            # Step 6 — Allowlist brokered user + ReBAC viewer grant on dataset.
            lyra.subjects.grants(demo_username).create(
                object_namespace="dataset",
                object_id=dataset_urn,
                relation="viewer",
            )
            grants = lyra.subjects.grants(demo_username).list()
            assert any(
                g.object_id == dataset_urn and g.relation == "viewer" for g in grants
            )

            # Step 7 — Submit federated job. The demo's job script lives
            # on the fleet's shared workdir; the test exercises the
            # submit path even when the script is trivial — what we're
            # asserting on is the audit_actor round-trip in step 8.
            result = lyra.jobs.run(
                target_cluster="ORION-m3-walkthrough",
                entrypoint="python -c 'print(\"m3 demo\")'",
            )

            # Step 8 — Demo-gate assertion: audit_actor names the
            # originating user (no system principal).
            assert result.status in {"SUCCEEDED", "FAILED"}, (
                f"Job ended in unexpected state {result.status!r}"
            )
            assert result.audit_actor is not None, (
                "audit_actor must round-trip on the receiver — this is "
                "the demo gate's load-bearing signal."
            )
            assert demo_username in result.audit_actor, (
                f"Expected audit_actor to contain {demo_username!r}, got "
                f"{result.audit_actor!r}. The job ran but the brokered-user "
                f"identity didn't round-trip; check the receiver-side "
                f"ext-authz + mesh-envelope wiring."
            )

        finally:
            # Best-effort cleanup so re-runs against the same fleet don't
            # pile up state. Each cleanup step swallows KamiwazaError —
            # the originating exception (if any) is what reaches the
            # test reporter.
            try:
                if dataset_urn:
                    try:
                        lyra.datasets.clear_gate(dataset_urn)
                    except KamiwazaError:
                        pass
                    try:
                        lyra.datasets.delete(dataset_urn)
                    except KamiwazaError:
                        pass
                try:
                    lyra.cluster.clear_execution_gate()
                except KamiwazaError:
                    pass
                try:
                    lyra.subjects.delete(demo_username, cascade_grants=True)
                except KamiwazaError:
                    pass
                if federation_id:
                    try:
                        lyra.federations[federation_id].disconnect()
                    except (KamiwazaError, AttributeError):
                        pass
            except Exception:
                # Last-resort: cleanup must not mask the test failure.
                pass
