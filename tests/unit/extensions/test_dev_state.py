"""Tests for DevStateFile (.kz-ext/dev-state.json) — ENG-3887 / §4.2.9."""

from __future__ import annotations

import json

import pytest

from kamiwaza_extensions.dev_state import (
    STEPS,
    DevState,
    mark_step,
    read_state,
    resume_message,
    state_path,
    write_state,
)


@pytest.mark.unit
class TestStatePathLocation:
    def test_lives_under_dot_kz_ext(self, tmp_path):
        p = state_path(tmp_path)
        assert p.parent.name == ".kz-ext"
        assert p.name == "dev-state.json"


@pytest.mark.extension_regression
@pytest.mark.unit
class TestReadWrite:
    def test_read_returns_none_when_missing(self, tmp_path):
        assert read_state(tmp_path) is None

    def test_write_then_read_round_trip(self, tmp_path):
        s = DevState(
            last_run_at="2026-04-28T20:00:00+00:00",
            last_revision="1.0.0-dev-abc123.1234567890",
            last_dev_name="hello-dev-b1e8a0",
            last_successful_step="apply",
            cluster="https://kamiwaza.test/api",
            extension_name="hello",
            deployer="jonathan@kamiwaza.ai",
        )
        write_state(tmp_path, s)
        out = read_state(tmp_path)
        assert out == s

    def test_write_creates_dot_kz_ext_dir(self, tmp_path):
        write_state(tmp_path, DevState(last_dev_name="x"))
        assert (tmp_path / ".kz-ext").is_dir()
        assert (tmp_path / ".kz-ext" / "dev-state.json").is_file()

    def test_corrupt_file_treated_as_missing(self, tmp_path):
        # An older or partially-written file shouldn't crash a re-invocation.
        path = state_path(tmp_path)
        path.parent.mkdir()
        path.write_text("not valid json {{{")
        assert read_state(tmp_path) is None

    def test_unknown_keys_ignored(self, tmp_path):
        # Forward compatibility: a future CLI may add fields. An older CLI
        # reading the same file must drop unknowns rather than crash.
        path = state_path(tmp_path)
        path.parent.mkdir()
        path.write_text(
            json.dumps(
                {
                    "last_dev_name": "hello-dev-x",
                    "last_successful_step": "apply",
                    "future_field": "ignored",
                }
            )
        )
        out = read_state(tmp_path)
        assert out is not None
        assert out.last_dev_name == "hello-dev-x"
        assert out.last_successful_step == "apply"


@pytest.mark.unit
class TestMarkStep:
    def test_invalid_step_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown dev step"):
            mark_step(
                tmp_path,
                "wrong",
                revision="x",
                dev_name="y",
                cluster="c",
                extension_name="e",
                deployer="d",
            )

    def test_records_step_and_metadata(self, tmp_path):
        s = mark_step(
            tmp_path,
            "build",
            revision="1.0.0-dev-abc",
            dev_name="hello-dev-b1",
            cluster="https://k.test/api",
            extension_name="hello",
            deployer="jonathan@kamiwaza.ai",
        )
        assert s.last_successful_step == "build"
        assert s.last_revision == "1.0.0-dev-abc"
        assert s.last_dev_name == "hello-dev-b1"
        assert s.deployer == "jonathan@kamiwaza.ai"
        # Persisted on disk
        assert read_state(tmp_path) == s

    def test_progresses_step_in_order(self, tmp_path):
        for step in STEPS:
            mark_step(
                tmp_path,
                step,
                revision="x",
                dev_name="y",
                cluster="c",
                extension_name="e",
                deployer="d",
            )
        out = read_state(tmp_path)
        assert out is not None
        assert out.last_successful_step == STEPS[-1]


@pytest.mark.unit
class TestIsStepComplete:
    def test_empty_state_no_steps_complete(self):
        s = DevState()
        for step in STEPS:
            assert s.is_step_complete(step) is False

    def test_apply_implies_build_and_push(self):
        s = DevState(last_successful_step="apply")
        assert s.is_step_complete("build") is True
        assert s.is_step_complete("push") is True
        assert s.is_step_complete("apply") is True
        assert s.is_step_complete("poll") is False


@pytest.mark.unit
class TestResumeMessage:
    def test_no_state_no_message(self):
        assert resume_message(None) is None

    def test_completed_run_no_message(self):
        # Last run finished all the way through poll — nothing to resume.
        s = DevState(
            last_successful_step="poll",
            last_run_at="2026-04-28T20:00:00+00:00",
        )
        assert resume_message(s) is None

    def test_partial_run_emits_resume_notice(self):
        s = DevState(
            last_successful_step="push",
            last_run_at="2026-04-28T20:00:00+00:00",
        )
        msg = resume_message(s)
        assert msg is not None
        assert "push" in msg
        assert "2026-04-28" in msg


# ---------------------------------------------------------------------------
# Review re-review PR #84 H4: actual resume now wired through
# `_is_resumable` in commands/dev.py. Test the helper directly here.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsResumable:
    def _state(self, **overrides) -> DevState:
        defaults = dict(
            last_run_at="2026-04-28T20:00:00+00:00",
            last_revision="rev-current",
            last_dev_name="my-app-dev-abc",
            last_successful_step="push",
            cluster="https://cluster.test/api",
            extension_name="my-app",
            deployer="alice@example.com",
        )
        defaults.update(overrides)
        return DevState(**defaults)

    def test_none_state_is_not_resumable(self):
        from kamiwaza_extensions.commands.dev import _is_resumable

        assert _is_resumable(None, "rev-x", "https://cluster.test/api") is False

    def test_matching_revision_and_cluster_is_resumable(self):
        from kamiwaza_extensions.commands.dev import _is_resumable

        s = self._state()
        assert _is_resumable(s, "rev-current", "https://cluster.test/api") is True

    def test_different_revision_is_not_resumable(self):
        # Different revision = different code = full pipeline. Skipping
        # build here would deploy stale image content.
        from kamiwaza_extensions.commands.dev import _is_resumable

        s = self._state(last_revision="rev-old")
        assert _is_resumable(s, "rev-current", "https://cluster.test/api") is False

    def test_different_cluster_is_not_resumable(self):
        # Different cluster = different registry = the prior push is in
        # the wrong place. Skipping push here would deploy a non-existent
        # image tag at the new cluster.
        from kamiwaza_extensions.commands.dev import _is_resumable

        s = self._state(cluster="https://other-cluster.test/api")
        assert _is_resumable(s, "rev-current", "https://cluster.test/api") is False


@pytest.mark.unit
class TestIsResumableStableId:
    """Review re-re-review PR #84 H1: ``_is_resumable`` keys off a
    *stable* identity extracted from the revision tag — not the raw
    timestamped tag. Without this, the default ``kz-ext dev`` workflow
    (which generates ``{version}-dev-{sha}.{epoch}`` afresh on every
    invocation) never resumes, defeating the purpose of dev-state."""

    def _state(self, last_revision: str) -> DevState:
        return DevState(
            last_run_at="2026-04-28T20:00:00+00:00",
            last_revision=last_revision,
            last_dev_name="my-app-dev-abc",
            last_successful_step="push",
            cluster="https://cluster.test/api",
            extension_name="my-app",
            deployer="alice@example.com",
        )

    def test_clean_sha_resumes_across_timestamps(self):
        # Same code (clean tree at sha abc1234) at two different
        # invocations — must resume. This is the central case the H1
        # fix exists for: default `kz-ext dev → fail → kz-ext dev` flow
        # without `--revision`.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state("1.0.0-dev-abc1234.1714000000")
        new_tag = "1.0.0-dev-abc1234.1714999999"
        assert _is_resumable(prior, new_tag, "https://cluster.test/api") is True

    def test_clean_sha_changed_does_not_resume(self):
        # Different sha = different code = full pipeline.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state("1.0.0-dev-abc1234.1714000000")
        new_tag = "1.0.0-dev-deadbef.1714999999"
        assert _is_resumable(prior, new_tag, "https://cluster.test/api") is False

    def test_dirty_tree_never_resumes(self):
        # Two `dirty` invocations could carry different uncommitted
        # content under the same `dirty` slug — refuse resume rather
        # than silently redeploy stale code.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state("1.0.0-dev-dirty.1714000000")
        new_tag = "1.0.0-dev-dirty.1714999999"
        assert _is_resumable(prior, new_tag, "https://cluster.test/api") is False

    def test_nogit_never_resumes(self):
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state("1.0.0-dev-nogit.1714000000")
        new_tag = "1.0.0-dev-nogit.1714999999"
        assert _is_resumable(prior, new_tag, "https://cluster.test/api") is False

    def test_custom_revision_pinned_resumes_when_identical(self):
        # `--revision rev-1` passed on both runs — user explicitly
        # opted into identity. The custom string is used verbatim.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state("rev-1")
        assert _is_resumable(prior, "rev-1", "https://cluster.test/api") is True

    def test_stable_revision_id_strips_epoch_for_clean_sha(self):
        from kamiwaza_extensions.commands.dev import _stable_revision_id

        assert (
            _stable_revision_id("1.0.0-dev-abc1234.1714000000") == "1.0.0-dev-abc1234"
        )
        assert _stable_revision_id("0.12.1-dev-deadbeef.1") == "0.12.1-dev-deadbeef"
        # Custom revision (no .epoch suffix) — return as-is.
        assert _stable_revision_id("rev-1") == "rev-1"
        # Dirty / nogit return None.
        assert _stable_revision_id("1.0.0-dev-dirty.1714000000") is None
        assert _stable_revision_id("1.0.0-dev-nogit.1714000000") is None


@pytest.mark.unit
class TestIsResumableSdkOverride:
    """Review re-re-review PR #84 H3: ``--sdk-repo`` is a power-user
    override that mutates the runtime-lib code baked into the image
    independently of the extension's git SHA. Two runs with the same
    extension revision but different ``--sdk-repo`` paths produce
    different image content — skipping build would silently redeploy
    stale SDK code. Disable resume whenever ``sdk_repo`` is set."""

    def _state(self) -> DevState:
        return DevState(
            last_run_at="2026-04-28T20:00:00+00:00",
            last_revision="1.0.0-dev-abc1234.1714000000",
            last_dev_name="my-app-dev-abc",
            last_successful_step="push",
            cluster="https://cluster.test/api",
            extension_name="my-app",
            deployer="alice@example.com",
        )

    def test_sdk_repo_set_disables_resume(self):
        from kamiwaza_extensions.commands.dev import _is_resumable

        s = self._state()
        # Same revision + same cluster — would normally resume — but
        # sdk_repo is set, so skip.
        assert (
            _is_resumable(
                s,
                "1.0.0-dev-abc1234.1714999999",
                "https://cluster.test/api",
                sdk_repo="/Users/dev/kamiwaza-sdk",
            )
            is False
        )

    def test_sdk_repo_none_allows_resume(self):
        from kamiwaza_extensions.commands.dev import _is_resumable

        s = self._state()
        assert (
            _is_resumable(
                s,
                "1.0.0-dev-abc1234.1714999999",
                "https://cluster.test/api",
                sdk_repo=None,
            )
            is True
        )

    def test_sdk_repo_default_argument_omitted(self):
        # Back-compat: callers that don't pass sdk_repo (legacy unit tests,
        # internal callers that don't know about override) get the prior
        # behaviour.
        from kamiwaza_extensions.commands.dev import _is_resumable

        s = self._state()
        assert (
            _is_resumable(
                s,
                "1.0.0-dev-abc1234.1714999999",
                "https://cluster.test/api",
            )
            is True
        )


@pytest.mark.unit
class TestIsResumableServiceFilterAndRegistry:
    """Review re-re-re-review PR #84 H1: ``_is_resumable`` must include
    every input that selects what gets built/pushed/deployed. A
    ``--service``-scoped first run only built that service; a later
    full run that resumed against it would deploy un-built services
    with tags that were never pushed. Same hazard for ``--sdk-repo``
    changes and registry changes."""

    def _state(self, **overrides) -> DevState:
        defaults = dict(
            last_run_at="2026-04-28T20:00:00+00:00",
            last_revision="1.0.0-dev-abc1234.1714000000",
            last_dev_name="my-app-dev-abc",
            last_successful_step="push",
            cluster="https://cluster.test/api",
            extension_name="my-app",
            deployer="alice@example.com",
            last_service=None,
            last_sdk_repo=None,
            last_registry="registry.test",
        )
        defaults.update(overrides)
        return DevState(**defaults)

    def test_partial_service_run_invalidates_full_resume(self):
        # Prior run was `--service backend` (only backend was built).
        # A later plain `kz-ext dev` (service=None) must NOT skip build —
        # frontend was never built at this revision.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state(last_service="backend")
        assert (
            _is_resumable(
                prior,
                rev_tag="1.0.0-dev-abc1234.1714999999",
                connection_url="https://cluster.test/api",
                registry="registry.test",
                service=None,
            )
            is False
        )

    def test_same_service_filter_resumes(self):
        # `--service backend` → fail → `--service backend` again is fine.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state(last_service="backend")
        assert (
            _is_resumable(
                prior,
                rev_tag="1.0.0-dev-abc1234.1714999999",
                connection_url="https://cluster.test/api",
                registry="registry.test",
                service="backend",
            )
            is True
        )

    def test_different_service_filter_invalidates_resume(self):
        # `--service backend` → fail → `--service frontend` —
        # frontend was never built at this revision.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state(last_service="backend")
        assert (
            _is_resumable(
                prior,
                rev_tag="1.0.0-dev-abc1234.1714999999",
                connection_url="https://cluster.test/api",
                registry="registry.test",
                service="frontend",
            )
            is False
        )

    def test_sdk_repo_change_invalidates_resume(self):
        # Prior run had no sdk-repo override; new run does → SDK code
        # in the image differs, so build must run.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state(last_sdk_repo=None)
        assert (
            _is_resumable(
                prior,
                rev_tag="1.0.0-dev-abc1234.1714999999",
                connection_url="https://cluster.test/api",
                registry="registry.test",
                sdk_repo="/Users/dev/kamiwaza-sdk",
            )
            is False
        )

    def test_sdk_repo_consistent_resumes(self):
        # Same sdk-repo path on both runs — resume is safe.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state(last_sdk_repo="/Users/dev/kamiwaza-sdk")
        assert (
            _is_resumable(
                prior,
                rev_tag="1.0.0-dev-abc1234.1714999999",
                connection_url="https://cluster.test/api",
                registry="registry.test",
                sdk_repo="/Users/dev/kamiwaza-sdk",
            )
            is True
        )

    def test_registry_change_invalidates_resume(self):
        # KAMIWAZA_REGISTRY=registry-a → fail →
        # KAMIWAZA_REGISTRY=registry-b: prior push is in the wrong
        # registry, so push must run again.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state(last_registry="registry-a")
        assert (
            _is_resumable(
                prior,
                rev_tag="1.0.0-dev-abc1234.1714999999",
                connection_url="https://cluster.test/api",
                registry="registry-b",
            )
            is False
        )

    def test_legacy_state_without_resume_inputs_invalidates_when_current_set(self):
        # An older dev-state file (pre-H1) doesn't carry service /
        # sdk_repo / registry. Reading drops the unknown keys and
        # leaves them at default (None / ""). When the current run
        # supplies a service or sdk_repo, the comparison must fail
        # (refuse resume) — old state didn't track this input, so we
        # can't safely conclude the prior run handled it.
        from kamiwaza_extensions.commands.dev import _is_resumable

        prior = self._state(
            last_service=None,
            last_sdk_repo=None,
            last_registry="",
        )
        assert (
            _is_resumable(
                prior,
                rev_tag="1.0.0-dev-abc1234.1714999999",
                connection_url="https://cluster.test/api",
                registry="registry.test",
                service="backend",
            )
            is False
        )


@pytest.mark.unit
class TestBuildPatchKwargsCarriesAnnotations:
    """Review re-review PR #84 H1: PATCH redeploy must refresh the
    deployer/revision/deployed-at annotations. Helper extracted from
    commands/dev.py for testability."""

    def _payload_with_annotations(
        self, annotations: dict | None, kamiwaza: object | None = None
    ) -> object:
        class _FakePayload:
            def __init__(self, model_extra, kamiwaza):
                self.model_extra = model_extra
                self.kamiwaza = kamiwaza

        return _FakePayload(
            {"annotations": annotations} if annotations else {},
            kamiwaza,
        )

    def test_carries_annotations_when_present(self):
        from kamiwaza_extensions.commands.dev import _build_patch_kwargs

        annotations = {
            "kamiwaza.io/deployer": "alice@example.com",
            "kamiwaza.io/revision": "1.0.0-dev-abc",
            "kamiwaza.io/deployed-at": "2026-04-29T10:00:00+00:00",
        }
        kwargs = _build_patch_kwargs(
            patch_services=["svc-a"],
            payload=self._payload_with_annotations(annotations),
        )
        assert kwargs["services"] == ["svc-a"]
        assert kwargs["annotations"] == annotations

    def test_omits_annotations_when_absent(self):
        from kamiwaza_extensions.commands.dev import _build_patch_kwargs

        kwargs = _build_patch_kwargs(
            patch_services=["svc-a"],
            payload=self._payload_with_annotations(None),
        )
        assert "annotations" not in kwargs
        assert kwargs["services"] == ["svc-a"]

    def test_patchextension_accepts_annotations_via_extra_allow(self):
        # Sanity: the PatchExtension schema must accept the annotations
        # kwarg via `extra="allow"`. If a future schema change locks
        # `extra="forbid"`, this test fails loudly.
        from kamiwaza_sdk.schemas.extensions import PatchExtension, PatchServiceSpec

        from kamiwaza_extensions.commands.dev import _build_patch_kwargs

        kwargs = _build_patch_kwargs(
            patch_services=[PatchServiceSpec(name="x")],
            payload=self._payload_with_annotations({"k": "v"}),
        )
        patch = PatchExtension(**kwargs)
        assert (patch.model_extra or {}).get("annotations") == {"k": "v"}

    def test_carries_kamiwaza_spec_so_patch_refreshes_tls_settings(self):
        """PR #92 iter-7: the existing CR persists from the original
        CREATE. If the developer flips TLS verify on the host (or
        upgrades SDK so dev-TLD auto-disable kicks in), PATCH must
        carry the new ``kamiwaza`` spec or the deployed
        ``KAMIWAZA_TLS_REJECT_UNAUTHORIZED`` stays stuck at the stale
        value forever."""
        from kamiwaza_extensions.commands.dev import _build_patch_kwargs

        # Sentinel object — _build_patch_kwargs doesn't introspect it.
        kamiwaza_spec = object()
        kwargs = _build_patch_kwargs(
            patch_services=["svc"],
            payload=self._payload_with_annotations(None, kamiwaza=kamiwaza_spec),
        )
        assert kwargs["kamiwaza"] is kamiwaza_spec

    def test_omits_kamiwaza_when_payload_has_none(self):
        from kamiwaza_extensions.commands.dev import _build_patch_kwargs

        kwargs = _build_patch_kwargs(
            patch_services=["svc"],
            payload=self._payload_with_annotations(None, kamiwaza=None),
        )
        assert "kamiwaza" not in kwargs

    def test_carries_sandbox_spec_so_patch_refreshes_sandbox_contract(self):
        """jxstanford PR #97 review H1: the existing CR persists from
        the original CREATE. If the developer toggles
        ``SANDBOX_BACKEND=kubernetes`` on or changes namespace/whitelist
        on a redeploy, PATCH must carry the new ``sandbox`` block so
        the operator can refresh sandbox RBAC. Without this carry-
        forward, sandbox config edits silently no-op until the user
        delete+recreates the extension."""
        from kamiwaza_extensions.commands.dev import _build_patch_kwargs

        class _FakePayload:
            model_extra = {
                "sandbox": {
                    "enabled": True,
                    "service_name": "sandbox-controller",
                    "namespace": "kamiwaza-sandboxes",
                }
            }
            kamiwaza = None

        kwargs = _build_patch_kwargs(
            patch_services=["svc"], payload=_FakePayload()
        )
        assert kwargs["sandbox"] == {
            "enabled": True,
            "service_name": "sandbox-controller",
            "namespace": "kamiwaza-sandboxes",
        }

    def test_omits_sandbox_when_payload_has_none(self):
        from kamiwaza_extensions.commands.dev import _build_patch_kwargs

        kwargs = _build_patch_kwargs(
            patch_services=["svc"],
            payload=self._payload_with_annotations(None),
        )
        assert "sandbox" not in kwargs

    def test_patch_service_specs_forwards_x_kamiwaza_overrides(self):
        """jxstanford PR #97 review H2: per-service overrides
        (healthCheck, automountServiceAccountToken,
        containerSecurityContext) must ride PATCH so the operator
        refreshes them on existing CRs after iterative redeploys."""
        from kamiwaza_sdk.schemas.extensions import ExtensionServiceSpec

        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        class _FakePayload:
            services = [
                ExtensionServiceSpec(
                    name="postgres",
                    image="postgres:15",
                    healthCheck={"tcpSocket": {"port": 5432}},
                    containerSecurityContext={
                        "runAsNonRoot": False,
                        "runAsUser": 0,
                    },
                ),
                ExtensionServiceSpec(
                    name="sandbox-controller",
                    image="reg/sc:dev",
                    automountServiceAccountToken=True,
                ),
                ExtensionServiceSpec(name="frontend", image="reg/fe:dev"),
            ]

        specs = _build_patch_service_specs(_FakePayload())
        by_name = {s.name: s for s in specs}

        pg_extra = by_name["postgres"].model_extra or {}
        assert pg_extra["healthCheck"] == {"tcpSocket": {"port": 5432}}
        assert pg_extra["containerSecurityContext"] == {
            "runAsNonRoot": False,
            "runAsUser": 0,
        }
        assert "automountServiceAccountToken" not in pg_extra

        sc_extra = by_name["sandbox-controller"].model_extra or {}
        assert sc_extra["automountServiceAccountToken"] is True
        assert "healthCheck" not in sc_extra
        assert "containerSecurityContext" not in sc_extra

        fe_extra = by_name["frontend"].model_extra or {}
        assert "healthCheck" not in fe_extra
        assert "automountServiceAccountToken" not in fe_extra
        assert "containerSecurityContext" not in fe_extra

    def test_patch_service_specs_preserves_automount_false(self):
        """``automountServiceAccountToken=False`` is a meaningful
        explicit setting (deny token mount). The ``is not None`` check
        in the helper must preserve it, not skip it as falsy."""
        from kamiwaza_sdk.schemas.extensions import ExtensionServiceSpec

        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        class _FakePayload:
            services = [
                ExtensionServiceSpec(
                    name="worker",
                    image="reg/worker:dev",
                    automountServiceAccountToken=False,
                ),
            ]

        specs = _build_patch_service_specs(_FakePayload())
        assert (specs[0].model_extra or {})["automountServiceAccountToken"] is False

    def test_patchextension_accepts_sandbox_via_extra_allow(self):
        """Sanity: PatchExtension must accept the sandbox kwarg via
        ``extra="allow"``."""
        from kamiwaza_sdk.schemas.extensions import PatchExtension, PatchServiceSpec

        from kamiwaza_extensions.commands.dev import _build_patch_kwargs

        class _FakePayload:
            model_extra = {"sandbox": {"enabled": True, "service_name": "sc"}}
            kamiwaza = None

        kwargs = _build_patch_kwargs(
            patch_services=[PatchServiceSpec(name="x")],
            payload=_FakePayload(),
        )
        patch = PatchExtension(**kwargs)
        assert (patch.model_extra or {}).get("sandbox") == {
            "enabled": True,
            "service_name": "sc",
        }
