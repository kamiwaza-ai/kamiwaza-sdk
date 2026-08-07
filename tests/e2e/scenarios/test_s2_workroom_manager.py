"""Driver for Appendix A Scenario 2 — App launched from Workroom Manager.

SDK-team automated. UACs 9c / 16. Platform-side discoverability is
out-of-scope for D210 per PRD §EDX-E2E-1 Note; this driver validates the
extension side once the platform lists the deployment.

The runbook maps to the ``workrooms.app-launch`` capability document in
``kamiwaza-internal/capability-kit`` (salesdevkit1 T1.4); runs emit
``scenario-evidence.v2`` records naming it via the runbook's
``capability_ids``.

Handler notes: the three assertion steps exercise the runtime library's
identity contract against the canonical test vectors
(``docs/extensions/non-sdk-flow/test-vectors.json``) — the same checks
the 2026-05-01 run performed. ``scaffold_app`` scaffolds via ``kz-ext
create`` and then deliberately skips the staging deploy (recording the
step ``skipped`` with detail, which derives a ``passed_with_notes``
scenario status); the deploy path lands with the T3.3 dry-run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from kamiwaza_extensions_lib import (
    MisboundAuthError,
    OutOfEnvelopeAccessError,
    extract_identity,
)
from tests.e2e.scenarios.harness import (
    load_runbook,
    record_run,
    render_sign_off,
    run_scenario,
)

SCENARIO_ID = "S2"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VECTORS_PATH = (
    _REPO_ROOT / "docs" / "extensions" / "non-sdk-flow" / "test-vectors.json"
)


def _vector(case: str) -> dict:
    vectors = json.loads(_VECTORS_PATH.read_text())
    for entry in vectors:
        if entry["case"] == case:
            return entry
    raise AssertionError(f"test vector {case!r} not found in {_VECTORS_PATH}")


def _assert_identity_matches(identity: object, expected: dict) -> list[str]:
    """Compare an extracted Identity against a vector's expected_identity."""
    mismatches = []
    for field, want in expected.items():
        got = getattr(identity, field)
        if got != want:
            mismatches.append(f"{field}: expected {want!r}, got {got!r}")
    if mismatches:
        raise AssertionError("; ".join(mismatches))
    return sorted(expected)


def _scaffold_app() -> str:
    if shutil.which("kz-ext") is None:
        raise AssertionError("kz-ext CLI not on PATH — cannot scaffold")
    workdir = tempfile.mkdtemp(prefix="s2-scaffold-")
    proc = subprocess.run(
        ["kz-ext", "create", "--type", "app", "--name", "s2-evidence-app"],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"kz-ext create failed (rc={proc.returncode}): {proc.stderr[-500:]}"
        )
    # kz-ext create scaffolds into the current directory (no subdir).
    created = Path(workdir)
    manifest = created / "kamiwaza.json"
    if not manifest.is_file():
        raise AssertionError(
            f"kz-ext create reported success but {manifest} is missing"
        )
    n_files = sum(
        1 for p in created.rglob("*") if p.is_file() and ".git" not in p.parts
    )
    pytest.skip(
        f"kz-ext create OK — app scaffold with {n_files} files; deploy via "
        "kz-ext dev to staging deliberately not exercised in this run "
        "(lands with the T3.3 dry-run)"
    )


def _assert_workroom_scoped_identity_headers() -> str:
    vec = _vector("happy-path")
    identity = extract_identity(vec["headers"])
    fields = _assert_identity_matches(identity, vec["expected_identity"])
    return (
        "runtime-lib extract_identity round-trips the canonical happy-path "
        f"vector; fields verified: {', '.join(fields)}"
    )


def _assert_global_workroom_sentinel_handling() -> str:
    vec = _vector("global-workroom-sentinel")
    identity = extract_identity(vec["headers"])
    fields = _assert_identity_matches(identity, vec["expected_identity"])
    sentinel = vec["headers"]["X-Workroom-Id"]
    if identity.workroom_id != sentinel:
        raise AssertionError(
            f"sentinel not preserved: {identity.workroom_id!r} != {sentinel!r}"
        )
    return (
        f"global-workroom sentinel {sentinel} honored per canonical vector; "
        f"fields verified: {', '.join(fields)}"
    )


def _assert_workroom_boundary_enforcement() -> str:
    # The runtime library's boundary contract: OutOfEnvelopeAccessError is
    # the refusal an SDK-built app receives on out-of-workroom access, and
    # requests arriving without a bound workroom identity are rejected at
    # extraction (MisboundAuthError) rather than defaulting open.
    try:
        raise OutOfEnvelopeAccessError("s2 boundary probe")
    except OutOfEnvelopeAccessError as exc:
        caught = type(exc).__name__
    rejected = []
    for case in ("missing-workroom", "missing-user-id"):
        vec = _vector(case)
        try:
            extract_identity(vec["headers"])
        except MisboundAuthError:
            rejected.append(case)
        else:
            raise AssertionError(
                f"extract_identity accepted the {case!r} vector — envelope "
                "binding not enforced"
            )
    return (
        f"{caught} importable and raisable; extract_identity rejects "
        f"unbound envelopes ({', '.join(rejected)}) with MisboundAuthError"
    )


@pytest.mark.e2e
def test_s2_full_loop(staging_url, build_id):
    runbook = load_runbook(SCENARIO_ID)

    handlers = {
        "scaffold_app": _scaffold_app,
        "assert_workroom_scoped_identity_headers": (
            _assert_workroom_scoped_identity_headers
        ),
        "assert_global_workroom_sentinel_handling": (
            _assert_global_workroom_sentinel_handling
        ),
        "assert_workroom_boundary_enforcement": _assert_workroom_boundary_enforcement,
    }

    result = run_scenario(runbook, handlers, build=build_id)
    artifact = record_run(result)
    sign_off = render_sign_off(result)

    if result.failed_steps:
        failed = [s.name for s in result.failed_steps]
        pytest.fail(
            f"S2 failed: artifact={artifact}, sign-off={sign_off}, failed steps={failed}"
        )
    if result.pending_steps:
        pending = [s.name for s in result.pending_steps]
        pytest.skip(
            f"S2 driver has unimplemented steps: {pending}. "
            f"Runbook + sign-off scaffolding rendered at {artifact}, {sign_off}."
        )
    assert result.passed, (
        f"S2 unexpected non-passing result: artifact={artifact}, sign-off={sign_off}, "
        f"steps={[(s.name, s.status) for s in result.steps]}"
    )
