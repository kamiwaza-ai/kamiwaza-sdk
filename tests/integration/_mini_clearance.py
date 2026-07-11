"""Shared fixtures + helpers for the ENG-8325 MiniClearanceGate live tests.

Layers 2 (single-cluster) and 3 (two-cluster federated) both prove the
install -> file-source -> gate-bind -> retrieve-through-gate path end to end
against a live cluster, using the trivial deterministic fixture:

    5 records [U, U, U, S, TS]  ->  post-gate counts  U:3/2  S:4/1  TS:5/0

The gate *logic* is covered offline in the kamiwaza repo
(tests/unit/services/authz/gates/test_mini_clearance_gate.py); these live layers
exercise the wheel install, the ``platform="file"`` parquet/csv source, the
dataset gate-binding, and the server-side gate invocation at retrieval time.

Every live prerequisite is a soft skip (never a hard fail on a contributor box):
the WS-M5 gate-packages PVC + a served 1.1.0 wheel, and a filesystem-source root
the ray-head can read.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

GATE_CLASSPATH = "acme_gates.mini_clearance_gate.MiniClearanceGate"
GATE_NAME = "mini_clearance_gate"
WHEEL_NAME = "acme_gates-1.1.0-py3-none-any.whl"
PACKAGE_SPEC = "acme-gates==1.1.0"

_FIXTURE = Path(__file__).parent / "fixtures" / "mini_clearance_records.json"

# persona clearance -> (included, redacted, allowed classifications)
KNOWN: dict[str, tuple[int, int, set[str]]] = {
    "U": (3, 2, {"U"}),
    "S": (4, 1, {"U", "S"}),
    "TS": (5, 0, {"U", "S", "TS"}),
}


def records() -> list[dict[str, Any]]:
    return json.loads(_FIXTURE.read_text())


def write_dataset_file(path: Path) -> str:
    """Write the 5-record fixture at ``path`` as parquet (fallback csv).

    The FilesystemAdapter infers the format from the extension. Returns the
    format actually written ("parquet" | "csv").
    """
    rows = records()
    if path.suffix.lower() == ".parquet":
        try:
            import pandas as pd  # noqa: PLC0415 — optional, only on the writer host

            pd.DataFrame(rows).to_parquet(path, index=False)
            return "parquet"
        except Exception:  # pandas/pyarrow absent -> csv fallback
            path = path.with_suffix(".csv")
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return "csv"


# ── gate-package install ────────────────────────────────────────────────────

def wheel_and_index() -> Optional[tuple[str, str]]:
    """(M5_TEST_WHEEL_DIR, M5_TEST_INDEX_URL) iff both set and the 1.1.0 wheel is present."""
    wheel_dir = os.getenv("M5_TEST_WHEEL_DIR", "").strip()
    index_url = os.getenv("M5_TEST_INDEX_URL", "").strip()
    if not wheel_dir or not index_url:
        return None
    if not (Path(wheel_dir) / WHEEL_NAME).exists():
        return None
    return wheel_dir, index_url


def _wheel_sha256(wheel_dir: str) -> str:
    digest = hashlib.sha256((Path(wheel_dir) / WHEEL_NAME).read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _already_installed(kz: Any) -> bool:
    """True iff acme-gates is installed with MiniClearanceGate's classpath present.

    The desired end-state is idempotent: the gate package being present (with our
    classpath) is what setup needs, regardless of how it got there. Checking this
    first makes the fixture resilient to a package left behind by an interrupted
    prior run — where an uninstall-first would 409 ``uninstall_blocked`` (an
    orphaned dataset still binds the gate) and a bare install would 409
    ``package_exists``.
    """
    try:
        listing = kz.gates.packages.list()
    except Exception:  # noqa: BLE001 — treat an unreadable listing as not-installed
        return False
    for pkg in getattr(listing, "items", listing) or []:
        if getattr(pkg, "name", None) == "acme-gates" and GATE_CLASSPATH in (
            getattr(pkg, "classpaths", None) or []
        ):
            return True
    return False


def install_gate_package(kz: Any, wheel_dir: str, index_url: str) -> None:
    """Ensure acme-gates==1.1.0 is installed and MiniClearanceGate is discoverable.

    The dataset gate-bind endpoint enforces the classpath allowlist against
    ``cluster_gate_packages.classpaths`` (populated by the install's discover
    step), so the package MUST be present before ``set_gate`` — otherwise the
    bind 403s ``classpath_not_allowed``. Idempotent: if a prior run already
    installed it (with our classpath) we keep it, since uninstalling it can be
    refused while an orphaned dataset still binds the gate.
    """
    if not _already_installed(kz):
        result = kz.gates.packages.install(
            PACKAGE_SPEC,
            hash_digest=_wheel_sha256(wheel_dir),
            index_url=index_url,
        )
        assert GATE_CLASSPATH in result.package.classpaths, (
            f"{GATE_CLASSPATH} not recorded in installed classpaths: {result.package.classpaths}"
        )
    gate = kz.gates.discover(GATE_CLASSPATH)
    assert gate.name == GATE_NAME


# ── clearance personas ──────────────────────────────────────────────────────

def declare_clearance_attribute(kz: Any) -> None:
    """Declare the ``clearance`` attribute in the realm vocabulary (idempotent).

    Required BEFORE binding a gate whose required_attributes() references it, and
    before seeding personas that carry it (ENG-4946)."""
    kz.cluster.declare_attribute("clearance", type="string")


def seed_local_persona(kz: Any, username: str, clearance: str) -> None:
    """Upsert a local subject carrying ``clearance`` (password == username)."""
    kz.subjects.upsert(username, attributes={"clearance": clearance}, password=username)


def grant_dataset_viewer(kz: Any, username: str, dataset_urn: str) -> None:
    """Grant the subject a viewer ReBAC tuple on the dataset (else retrieval 404s
    at the seam before the gate runs)."""
    kz.subjects.grants(username).create(
        object_namespace="dataset", object_id=dataset_urn, relation="viewer"
    )


class _NoCacheTokenStore:
    """No-op token store so each persona authenticates fresh (no on-disk bleed
    between the U/S/TS clients). Duck-types the SDK TokenStore contract."""

    def load(self) -> None:
        return None

    def save(self, token: Any) -> None:  # noqa: ARG002
        return None

    def clear(self) -> None:
        return None


def authed_client(base_url: str, username: str, password: str, *, verify: bool) -> Any:
    """A KamiwazaClient authenticated as (username/password) via the SDK's
    password-grant authenticator — the canonical live-test auth path (mirrors
    conftest's live_kamiwaza_client)."""
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.authentication import UserPasswordAuthenticator

    client = KamiwazaClient(base_url=base_url, verify=verify)
    client.authenticator = UserPasswordAuthenticator(
        username, password, client._auth_service, token_store=_NoCacheTokenStore()  # type: ignore[arg-type]
    )
    return client


def raw_token_client(base_url: str, token: str, *, verify: bool) -> Any:
    """A KamiwazaClient presenting a raw bearer token verbatim (for the
    fabrication-negative: a token NOT signed by the shared realm)."""
    from kamiwaza_sdk import KamiwazaClient

    return KamiwazaClient(base_url=base_url, api_key=token, verify=verify)


# ── file-backed gated dataset ───────────────────────────────────────────────

def create_file_dataset(kz: Any, name: str, file_path: str) -> str:
    """Create a ``platform="file"`` dataset pointing at ``file_path`` and bind
    MiniClearanceGate. Returns the dataset URN. Install must have run first."""
    urn = kz.datasets.create(
        name=name,
        platform="file",
        properties={"path": file_path},
    )
    kz.datasets.set_gate(urn, type=GATE_CLASSPATH, config={})
    return urn


def retrieve_through_gate(client: Any, dataset_urn: str) -> tuple[list[dict], dict]:
    """Retrieve the dataset through the gate; return (rows, gate_audit summary).

    Drains the SSE stream: ``chunk`` events carry ``records`` and a ``metadata``
    dict whose ``gate_audit`` is the runner footer {gate, included, redacted,
    total}. Summed across chunks (one chunk for the 5-row fixture).
    """
    from kamiwaza_sdk.schemas.retrieval import RetrievalRequest

    job = client.retrieval.create_job(RetrievalRequest(dataset_urn=dataset_urn))
    rows: list[dict] = []
    included = redacted = total = 0
    saw_audit = False
    for event in client.retrieval.stream_events(job.job_id):
        if event.event != "chunk":
            continue
        data = event.data or {}
        rows.extend(data.get("records") or data.get("rows") or [])
        gate_audit = (data.get("metadata") or {}).get("gate_audit")
        if gate_audit:
            saw_audit = True
            included += int(gate_audit.get("included", 0))
            redacted += int(gate_audit.get("redacted", 0))
            total += int(gate_audit.get("total", 0))
    summary = (
        {"included": included, "redacted": redacted, "total": total} if saw_audit else {}
    )
    return rows, summary


def initiator_cluster_uuid(receiver: Any, receiver_fed_id: str) -> Optional[str]:
    """The initiator cluster's UUID, for building brokered ``external_id``s.

    Sourced from the RECEIVER's federation record (``remote_cluster_id``, which
    the /pair handshake populates), NOT ``initiator.cluster.diagnose()`` (that
    returns a ClusterDiagnostics with no cluster-id field) nor
    ``cluster.capabilities()`` (403 ``not_authorized_to_probe_cluster`` for a
    plain admin). Matched by the receiver-side federation *id* — the /pair
    handshake overwrites the receiver's ``remote_cluster_name`` with the
    initiator's cluster name, so a lookup-by-name fails post-pair.
    ``GET /cluster/federations`` is the widened any-authenticated surface.
    Returns None if the record/field isn't present yet.
    """
    feds = receiver._request("GET", "/cluster/federations") or []
    if isinstance(feds, dict):  # paginated {"items": [...]} shape
        feds = feds.get("items") or []
    record = next((f for f in feds if str(f.get("id")) == str(receiver_fed_id)), None)
    cluster_uuid = (record or {}).get("remote_cluster_id")
    return str(cluster_uuid) if cluster_uuid else None


def parse_mesh_retrieval_result(result: Any, initiator: Any, fed_name: str) -> tuple[list[dict], dict]:
    """Parse a mesh retrieval-jobs response into (rows, gate_audit summary).

    The federated job-result seam emits ``gate_audit`` as a LIST (one entry per
    gated target dataset), unlike the inline single-dict retrieval footer — so
    branch on the actual shape. Kept defensive: this is the live-iteration seam
    that only exercises once the mesh data-plane returns records.
    """
    del initiator, fed_name  # reserved for a poll/stream variant if the mesh
    # returns a job handle rather than an inline result

    included = redacted = total = 0
    saw = False

    def _absorb(entry: Any) -> None:
        nonlocal included, redacted, total, saw
        if isinstance(entry, list):
            for item in entry:
                _absorb(item)
        elif isinstance(entry, dict):
            saw = True
            included += int(entry.get("included", 0))
            redacted += int(entry.get("redacted", 0))
            total += int(entry.get("total", 0))

    rows: list[dict] = []
    if isinstance(result, dict):
        rows = list(result.get("records") or result.get("rows") or [])
        meta = result.get("metadata") or result
        _absorb(meta.get("gate_audit"))
    summary = {"included": included, "redacted": redacted, "total": total} if saw else {}
    return rows, summary


def assert_persona_result(clearance: str, rows: list[dict], gate_audit: dict) -> None:
    """Assert the known post-gate counts + zero leakage for a persona."""
    included, redacted, allowed = KNOWN[clearance]
    assert gate_audit, "no gate_audit footer in retrieval stream — gate not invoked?"
    assert gate_audit["included"] == included, gate_audit
    assert gate_audit["redacted"] == redacted, gate_audit
    assert gate_audit["total"] == included + redacted
    # zero leakage: nothing above the caller's clearance survives
    leaked = [r for r in rows if str(r.get("classification", "")).upper() not in allowed]
    assert not leaked, f"{clearance} caller leaked rows above clearance: {leaked}"
