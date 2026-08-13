"""Driver for Appendix A Scenario 2 — App launched from Workroom Manager.

SDK-team automated. UACs 9c / 16. Platform-side discoverability is
out-of-scope for D210 per PRD §EDX-E2E-1 Note; this driver validates the
extension side once the platform lists the deployment.

The runbook maps to the ``workrooms.app-launch`` capability document in
``kamiwaza-internal/capability-kit`` (salesdevkit1 T1.4); runs emit
``scenario-evidence.v2`` records naming it via the runbook's
``capability_ids``. That mapping is pinned by a unit test below so CI
guards the join even though the full-loop driver is e2e-only.

Handler notes: the two identity steps exercise the runtime library's
identity contract against the canonical test vectors
(``docs/extensions/non-sdk-flow/test-vectors.json``). The boundary step
verifies that ``extract_identity`` rejects unbound envelopes with
``MisboundAuthError``, then records itself ``skipped``: no
``kamiwaza_extensions_lib`` code path raises ``OutOfEnvelopeAccessError``
yet, so the runbook's "assert the runtime lib raises
OutOfEnvelopeAccessError" cannot be exercised honestly — the step stays
skipped (deriving ``passed_with_notes``) until that enforcement path
lands. ``scaffold_app`` scaffolds via ``kz-ext create`` and deliberately
skips the staging deploy (the deploy path lands with the T3.3 dry-run);
a missing ``kz-ext`` CLI is treated as a provisioning gap (skip), not a
capability failure.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NoReturn

import pytest

from kamiwaza_extensions_lib import (
    Identity,
    MisboundAuthError,
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


class _Missing:
    def __repr__(self) -> str:
        return "<no such field on Identity>"


_MISSING = _Missing()


@functools.lru_cache(maxsize=1)
def _load_vectors() -> tuple[dict, ...]:
    return tuple(json.loads(_VECTORS_PATH.read_text()))


def _vector(case: str) -> dict:
    for entry in _load_vectors():
        if entry["case"] == case:
            return entry
    raise AssertionError(f"test vector {case!r} not found in {_VECTORS_PATH}")


def _unbound_envelope_cases() -> list[str]:
    """Cases the vectors file tags as misbound-auth rejections.

    Derived from ``should_fail_class`` so a newly added misbound vector is
    covered automatically instead of silently ignored.
    """
    return [
        entry["case"]
        for entry in _load_vectors()
        if entry.get("should_fail_class") == "misbound_auth"
    ]


def _assert_identity_matches(identity: Identity, expected: dict) -> list[str]:
    """Compare an extracted Identity against a vector's expected_identity."""
    mismatches = [
        f"{field}: expected {want!r}, got {got!r}"
        for field, want in expected.items()
        if (got := getattr(identity, field, _MISSING)) != want
    ]
    if mismatches:
        raise AssertionError("; ".join(mismatches))
    return sorted(expected)


def _scaffold_app() -> NoReturn:
    if shutil.which("kz-ext") is None:
        pytest.skip(
            "kz-ext CLI not on PATH — provisioning gap in this environment, "
            "not a capability failure; install the SDK CLI to exercise the "
            "scaffold step"
        )
    with tempfile.TemporaryDirectory(prefix="s2-scaffold-") as workdir:
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
        # Scaffolder.create targets cwd itself when cwd is empty (true for a
        # fresh TemporaryDirectory) and cwd/{name} otherwise; accept either
        # so a scaffolder behavior change surfaces as a wrong-shape failure,
        # not a false negative here.
        created = Path(workdir)
        candidates = (
            created / "kamiwaza.json",
            created / "s2-evidence-app" / "kamiwaza.json",
        )
        manifest = next((m for m in candidates if m.is_file()), None)
        if manifest is None:
            raise AssertionError(
                "kz-ext create reported success but kamiwaza.json is missing "
                f"(looked in {', '.join(str(c) for c in candidates)})"
            )
        scaffold_root = manifest.parent
        n_files = sum(
            1 for p in scaffold_root.rglob("*") if p.is_file() and ".git" not in p.parts
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
    sentinel = vec["expected_identity"]["workroom_id"]
    return (
        f"global-workroom sentinel {sentinel} honored per canonical vector; "
        f"fields verified: {', '.join(fields)}"
    )


def _check_unbound_envelope_rejection() -> list[str]:
    """Assert extract_identity rejects unbound envelopes; return the cases."""
    cases = _unbound_envelope_cases()
    if not cases:
        raise AssertionError(
            f"no misbound_auth-tagged vectors in {_VECTORS_PATH} — the "
            "unbound-envelope check would be vacuous"
        )
    rejected = []
    for case in cases:
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
    return rejected


def _assert_workroom_boundary_enforcement() -> NoReturn:
    # Requests arriving without a bound workroom identity must be rejected
    # at extraction (MisboundAuthError) rather than defaulting open — that
    # half is asserted for real. The step's headline contract ("the runtime
    # lib raises OutOfEnvelopeAccessError on out-of-workroom access") has no
    # code path in kamiwaza_extensions_lib that raises it yet, so the step
    # records skipped instead of claiming boundary coverage it never
    # exercised; it flips to a hard assertion when the enforcement path
    # lands (T3.3 dry-run).
    rejected = _check_unbound_envelope_rejection()
    pytest.skip(
        "unbound envelopes rejected with MisboundAuthError "
        f"({', '.join(rejected)}); OutOfEnvelopeAccessError enforcement not "
        "yet exercisable — no kamiwaza_extensions_lib code path raises it, "
        "so this step stays skipped rather than recording boundary coverage "
        "it did not verify (pending T3.3)"
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


# ---------------------------------------------------------------------------
# CI-visible unit coverage (make test deselects e2e; these run everywhere).
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_s2_runbook_maps_to_workrooms_app_launch():
    """Pin the PR's headline deliverable: S2 names the capability it feeds.

    The evidence schema warns that a near-miss id (e.g. the kebab-only
    ``workroom-app-launch``) silently joins to nothing in capability-kit,
    so the exact mapping needs a guard.
    """
    assert load_runbook(SCENARIO_ID)["capability_ids"] == ["workrooms.app-launch"]


@pytest.mark.unit
def test_workroom_scoped_identity_headers_handler():
    detail = _assert_workroom_scoped_identity_headers()
    assert "happy-path" in detail


@pytest.mark.unit
def test_global_workroom_sentinel_handler():
    detail = _assert_global_workroom_sentinel_handling()
    assert "sentinel" in detail


@pytest.mark.unit
def test_unbound_envelopes_rejected():
    assert sorted(_check_unbound_envelope_rejection()) == [
        "missing-user-id",
        "missing-workroom",
    ]


@pytest.mark.unit
def test_boundary_step_skips_pending_enforcement_path():
    """The boundary step must not record coverage it cannot exercise."""
    with pytest.raises(pytest.skip.Exception, match="OutOfEnvelopeAccessError"):
        _assert_workroom_boundary_enforcement()


class _FakeProc:
    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr


@pytest.mark.unit
def test_scaffold_app_missing_cli_skips(monkeypatch):
    """A missing kz-ext CLI is a provisioning gap, not a capability failure."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(pytest.skip.Exception, match="provisioning gap"):
        _scaffold_app()


@pytest.mark.unit
def test_scaffold_app_nonzero_rc_fails(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/kz-ext")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeProc(2, "registry unreachable")
    )
    with pytest.raises(AssertionError, match=r"rc=2.*registry unreachable"):
        _scaffold_app()


@pytest.mark.unit
def test_scaffold_app_missing_manifest_fails(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/kz-ext")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(0))
    with pytest.raises(AssertionError, match="kamiwaza.json is missing"):
        _scaffold_app()


@pytest.mark.unit
def test_scaffold_app_success_skips_deploy(monkeypatch):
    """The success path always ends in the deliberate deploy skip."""

    def fake_create(cmd, cwd=None, **kwargs):
        Path(cwd, "kamiwaza.json").write_text("{}")
        Path(cwd, "app.py").write_text("")
        return _FakeProc(0)

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/kz-ext")
    monkeypatch.setattr(subprocess, "run", fake_create)
    with pytest.raises(
        pytest.skip.Exception, match=r"2 files.*deliberately not exercised"
    ):
        _scaffold_app()


@pytest.mark.unit
def test_identity_mismatches_aggregate_instead_of_attribute_error():
    identity = extract_identity(_vector("happy-path")["headers"])
    with pytest.raises(AssertionError) as excinfo:
        _assert_identity_matches(
            identity, {"user_id": "someone-else", "not_a_field": "x"}
        )
    message = str(excinfo.value)
    assert "user_id" in message
    assert "not_a_field" in message
