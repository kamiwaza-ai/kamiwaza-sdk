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
the WS-M5 gate-packages PVC + the SDK-owned fixture provisioner output, and a
filesystem-source root the ray-head can read. Set ``M5_TEST_KUBECTL`` to
materialize all package versions and the deterministic dataset at session
startup, or run ``python -m tests.integration._gate_fixture provision``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any, NoReturn, Optional

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.services.federation_credentials import federation_credential_headers
from kamiwaza_sdk.validation.federation_fixture import (
    GATE_CLASSPATH,
    GATE_NAME,
    GATE_PACKAGE_NAME,
    GATE_PACKAGE_SPEC,
    KNOWN as SDK_KNOWN,
    records as sdk_records,
)

WHEEL_NAME = "acme_gates-1.1.0-py3-none-any.whl"
PACKAGE_SPEC = GATE_PACKAGE_SPEC

# FastAPI auth denials are normally a few hundred bytes. Keep enough room for
# structured detail while preventing a peer from making this live-test helper
# retain or print an unbounded streaming response.
_MESH_STREAM_ERROR_BODY_LIMIT_BYTES = 8 * 1024
_MESH_STREAM_ERROR_CHUNK_BYTES = 1024
_MESH_STREAM_ERROR_TRUNCATION_MARKER = (
    f"...[truncated after {_MESH_STREAM_ERROR_BODY_LIMIT_BYTES} bytes]"
)


class _MeshStreamAPIError(APIError):
    """Raw mesh stream failure with an explicit diagnostic truncation signal."""

    response_truncated: bool


# persona clearance -> (included, redacted, allowed classifications)
KNOWN: dict[str, tuple[int, int, set[str]]] = {
    clearance: (included, len(sdk_records()) - included, set(allowed))
    for clearance, (included, allowed) in SDK_KNOWN.items()
}
_EXACT_FIXTURE_CLEARANCES = frozenset(KNOWN)


def records() -> list[dict[str, Any]]:
    return [dict(row) for row in sdk_records()]


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
        if getattr(pkg, "name", None) == GATE_PACKAGE_NAME and GATE_CLASSPATH in (
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
        object_namespace="dataset",
        object_id=dataset_urn,
        relation="viewer",
        attested=True,
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
        username,
        password,
        client._auth_service,
        token_store=_NoCacheTokenStore(),  # type: ignore[arg-type]
    )
    return client


def raw_token_client(base_url: str, token: str, *, verify: bool) -> Any:
    """A KamiwazaClient presenting a raw bearer token verbatim (for the
    fabrication-negative: a token NOT signed by the shared realm)."""
    from kamiwaza_sdk import KamiwazaClient

    return KamiwazaClient(base_url=base_url, api_key=token, verify=verify)


def shared_realm_token(
    issuer: str,
    client_id: str,
    username: str,
    password: str,
    *,
    client_secret: Optional[str] = None,
    verify: Any = True,
) -> str:
    """Mint an access token via ROPC (direct-access-grants) against a shared
    realm's token endpoint.

    A shared_idp mesh caller must present a token signed by the SHARED realm
    (``issuer`` == the federation's shared_issuer_url): the mesh forwards the
    caller's bearer verbatim, and the receiver validates it against the shared
    realm's JWKS, so a local-realm token's kid is rejected (not in the shared
    JWKS). The token also carries the fixture realm's projected ``clearance``
    and explicit ``tenant_id=__default__`` claims; the initiator edge packs
    ``clearance`` into X-User-Attributes for the gate.
    """
    import requests  # noqa: PLC0415 — only needed on the persona-auth path

    data = {
        "grant_type": "password",
        "client_id": client_id,
        "username": username,
        "password": password,
        "scope": "openid",
    }
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(
        f"{issuer.rstrip('/')}/protocol/openid-connect/token",
        data=data,
        verify=verify,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def jwt_sub(token: str) -> str:
    """The ``sub`` claim of a JWT (no signature verification — the server does
    that). The receiver keys the brokered user on ``<sub>@<cluster-uuid>``."""
    import base64  # noqa: PLC0415

    payload = token.split(".")[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    return str(json.loads(base64.urlsafe_b64decode(payload)).get("sub", ""))


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


def retrieve_through_gate(
    client: Any, dataset_urn: str
) -> tuple[list[dict], list[dict]]:
    """Retrieve the dataset through the gate; return rows and actual audit footers.

    Drains the SSE stream: ``chunk`` events carry ``records`` and a ``metadata``
    dict whose ``gate_audit`` is the runner footer.

    ENG-8859 reduced that footer to a single ``filtered`` boolean. The counts it
    used to carry (``included`` / ``redacted`` / ``total``) are now always
    ``null`` — in a classified setting the *volume* of withheld material is
    itself a disclosure to the caller who was denied it — so summing them here
    raised ``TypeError: int() argument must be ... not 'NoneType'``.

    Counting from ``rows`` instead is the stronger assertion anyway: it checks
    what actually arrived rather than what the footer claimed about it.
    """
    from kamiwaza_sdk.schemas.retrieval import RetrievalRequest

    job = client.retrieval.create_job(
        RetrievalRequest(dataset_urn=dataset_urn, transport="sse")
    )
    rows: list[dict] = []
    gate_audits: list[dict] = []
    for event in client.retrieval.stream_events(job.job_id):
        if event.event != "chunk":
            continue
        data = event.data or {}
        rows.extend(data.get("data") or data.get("records") or data.get("rows") or [])
        gate_audit = (data.get("metadata") or {}).get("gate_audit")
        if isinstance(gate_audit, dict):
            gate_audits.append(gate_audit)
    return rows, gate_audits


def _read_bounded_stream_error_body(response: Any) -> tuple[bytes, bool]:
    retained = bytearray()
    truncated = False
    for chunk in response.iter_content(chunk_size=_MESH_STREAM_ERROR_CHUNK_BYTES):
        if not chunk:
            continue
        remaining = _MESH_STREAM_ERROR_BODY_LIMIT_BYTES - len(retained)
        if len(chunk) <= remaining:
            retained.extend(chunk)
            continue
        retained.extend(chunk[:remaining])
        truncated = True
        break
    return bytes(retained), truncated


def _decode_stream_error_body(response: Any) -> tuple[str, Any, bool]:
    body, truncated = _read_bounded_stream_error_body(response)
    text = body.decode("utf-8", errors="replace")
    if truncated:
        return f"{text}{_MESH_STREAM_ERROR_TRUNCATION_MARKER}", None, True
    try:
        return text, json.loads(text), False
    except json.JSONDecodeError:
        return text, None, False


def _raise_mesh_stream_error(response: Any) -> NoReturn:
    diagnostic, response_data, response_truncated = _decode_stream_error_body(response)
    error = _MeshStreamAPIError(
        f"mesh retrieval stream returned {response.status_code}: {diagnostic}",
        status_code=response.status_code,
        response_text=diagnostic,
        response_data=response_data,
    )
    error.response_truncated = response_truncated
    raise error


def mesh_retrieve_through_gate(
    persona_client: Any,
    base_url: str,
    token: str,
    fed_name: str,
    dataset_urn: str,
    *,
    verify: Any,
) -> tuple[list[dict], list[dict]]:
    """Create a retrieval job over the mesh, then drain its gated SSE stream over
    the mesh — the L3 (two-cluster) analogue of ``retrieve_through_gate``.

    The POST explicitly requests SSE so it returns an async job handle and the
    gated ``records`` + ``gate_audit`` footer arrive on
    ``GET /mesh/{fed}/api/retrieval/jobs/{id}/stream``. The mesh proxy forwards
    both verbatim (StreamingResponse over ``aiter_raw``). The create goes through
    the SDK client so a 401/403/404 raises APIError for
    ``_mesh_call_or_skip`` to classify; the stream is a raw SSE GET (the SDK only
    streams local retrieval paths). Returns every actual footer in stream order;
    ``gate_audit`` is absorbed as either the inline single-dict footer or the
    federated list-of-dicts seam.
    """
    import requests

    credential_headers = federation_credential_headers(fed_name)
    job = persona_client._request(
        "POST",
        f"/mesh/{fed_name}/api/retrieval/jobs",
        json={"dataset_urn": dataset_urn, "transport": "sse"},
        **({"headers": credential_headers} if credential_headers else {}),
    )
    if isinstance(job, dict):
        job_id = job.get("job_id") or job.get("id")
    else:
        job_id = getattr(job, "job_id", None) or getattr(job, "id", None)
    assert job_id, f"mesh create-job returned no job id: {job!r}"

    rows: list[dict] = []
    gate_audits: list[dict] = []

    def _absorb(entry: Any) -> None:
        # The job-result seam emits a LIST of per-gate footers, while inline/SSE
        # emits a single dict. Preserve each footer instead of reconstructing it.
        if isinstance(entry, list):
            for item in entry:
                _absorb(item)
        elif isinstance(entry, dict):
            gate_audits.append(entry)

    url = f"{base_url}/mesh/{fed_name}/api/retrieval/jobs/{job_id}/stream"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        **credential_headers,
    }
    with requests.get(
        url, headers=headers, stream=True, verify=verify, timeout=120
    ) as sr:
        if sr.status_code in (403, 404):
            _raise_mesh_stream_error(sr)
        sr.raise_for_status()
        event: Optional[str] = None
        data_lines: list[str] = []
        for raw in sr.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            if raw == "":  # SSE event terminator (blank line)
                if data_lines and event == "chunk":
                    payload = json.loads("\n".join(data_lines))
                    rows.extend(
                        payload.get("data")
                        or payload.get("records")
                        or payload.get("rows")
                        or []
                    )
                    _absorb((payload.get("metadata") or {}).get("gate_audit"))
                event, data_lines = None, []
                continue
            if raw.startswith(":"):  # SSE comment / heartbeat
                continue
            if raw.startswith("event:"):
                event = raw[len("event:") :].strip()
            elif raw.startswith("data:"):
                data_lines.append(raw[len("data:") :].lstrip())

    return rows, gate_audits


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


def _assert_exact_fixture_rows(
    clearance: str,
    rows: list[dict],
    allowed: set[str],
) -> None:
    if clearance not in _EXACT_FIXTURE_CLEARANCES:
        return
    expected_rows = sorted(
        (
            record
            for record in records()
            if str(record.get("classification", "")).upper() in allowed
        ),
        key=lambda record: str(record.get("id", "")),
    )
    actual_rows = sorted(rows, key=lambda record: str(record.get("id", "")))
    assert actual_rows == expected_rows, (
        f"{clearance} caller received the wrong post-gate rows: "
        f"expected={expected_rows!r} actual={actual_rows!r}"
    )


def assert_persona_result(
    clearance: str, rows: list[dict], gate_audits: list[dict]
) -> None:
    """Assert the exact post-gate rows, footer contract, and zero leakage.

    Counts are asserted against the rows that ARRIVED, not against the footer's
    claim about them — the footer no longer carries counts (ENG-8859), and
    checking the data directly is what the test was really for. The footer
    contributes one bit, ``filtered``, which must agree with whether this
    persona has anything withheld.
    """
    included, redacted, allowed = KNOWN[clearance]
    assert gate_audits, "no gate_audit footer in retrieval stream — gate not invoked?"
    assert len(rows) == included, (
        f"expected {included} rows for {clearance}, got {len(rows)}"
    )
    _assert_exact_fixture_rows(clearance, rows, allowed)
    assert any(bool(audit.get("filtered")) for audit in gate_audits) is (
        redacted > 0
    ), gate_audits
    for gate_audit in gate_audits:
        # The deprecated count keys must be present-and-null, not resurrected.
        assert all(
            key in gate_audit and gate_audit[key] is None
            for key in ("included", "redacted", "total", "gate")
        ), f"deprecated gate_audit keys must be present and null: {gate_audit}"
    # zero leakage: nothing above the caller's clearance survives
    leaked = [
        r for r in rows if str(r.get("classification", "")).upper() not in allowed
    ]
    assert not leaked, f"{clearance} caller leaked rows above clearance: {leaked}"
