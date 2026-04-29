"""Tests for CatalogDedupGuard + revision plumbing (ENG-3884 / §4.2.5)."""

from __future__ import annotations

import pytest

from kamiwaza_extensions.catalog_publisher import (
    CatalogDedupError,
    CatalogDedupGuard,
)
from kamiwaza_extensions.exit_codes import ExitCode


def _entry(name: str, version: str, revision: str | None = None) -> dict:
    out = {"name": name, "version": version, "description": "x"}
    if revision is not None:
        out["revision"] = revision
    return out


@pytest.mark.unit
class TestRevisionGrammar:
    # TS-24: revision grammar enforced; invalid rejected with exit 2
    @pytest.mark.parametrize(
        "valid",
        [
            "a", "0", "abc-def", "1.0.0", "release-0.12.1",
            "0.12.1-dev-a1b2c3d.1714000000",
            "abcdef0123456789",
            "a" * 64,
        ],
    )
    def test_accepts_valid_revisions(self, valid):
        CatalogDedupGuard.validate_revision(valid)  # no raise

    @pytest.mark.parametrize(
        "invalid",
        [
            "",  # empty
            "-foo",  # leading hyphen not allowed
            ".foo",  # leading dot not allowed
            "Foo",  # uppercase
            "foo bar",  # space
            "foo_bar",  # underscore
            "foo/bar",  # slash
            "a" * 65,  # too long
        ],
    )
    def test_rejects_invalid_revisions(self, invalid):
        with pytest.raises(ValueError, match="Invalid revision"):
            CatalogDedupGuard.validate_revision(invalid)


@pytest.mark.unit
class TestDedupCheck:
    # TS-22: duplicate (name, semver, revision) → CatalogDedupError → exit 2
    def test_rejects_duplicate_triple(self):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        with pytest.raises(CatalogDedupError) as exc_info:
            guard.check(existing, _entry("hello", "1.0.0", revision="abc123"))
        # Carries enough context to print an actionable error
        assert exc_info.value.extension_name == "hello"
        assert exc_info.value.version == "1.0.0"
        assert exc_info.value.revision == "abc123"
        # CatalogDedupError is a ValueError subclass — CLI maps to exit 2
        assert isinstance(exc_info.value, ValueError)
        # Sanity check: ExitCode.VALIDATION is 2
        assert int(ExitCode.VALIDATION) == 2

    # TS-23: --force overrides dedup with warning
    def test_force_bypasses_dedup_with_warning(self, capsys):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        # Should not raise
        guard.check(
            existing,
            _entry("hello", "1.0.0", revision="abc123"),
            force=True,
        )

    def test_inserts_when_revision_differs(self):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        guard.check(existing, _entry("hello", "1.0.0", revision="def456"))

    def test_inserts_when_version_differs(self):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        guard.check(existing, _entry("hello", "1.0.1", revision="abc123"))

    def test_inserts_when_name_differs(self):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        guard.check(existing, _entry("other", "1.0.0", revision="abc123"))

    def test_no_revision_skips_dedup(self):
        # Entries published without --revision (e.g. release publishes via
        # ENG-3591's flag-omitted path) are not subject to dedup.
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0")]
        guard.check(existing, _entry("hello", "1.0.0"))  # no raise

    def test_invalid_revision_raises_value_error_before_lookup(self):
        guard = CatalogDedupGuard()
        # Even with an empty existing list, a malformed revision fails early.
        with pytest.raises(ValueError, match="Invalid revision"):
            guard.check([], _entry("hello", "1.0.0", revision="BAD CASE"))


@pytest.mark.unit
class TestForwardCompatGardenApp:
    # TS-25: Older CLI reads newer catalog with revision field
    def test_garden_app_accepts_revision_field(self):
        from kamiwaza_sdk.schemas.apps import GardenApp

        # Newer catalog entry includes a `revision` field
        payload = {
            "name": "hello",
            "version": "1.0.0",
            "description": "test",
            "compose_yml": "services: {}",
            "revision": "abc123",
        }
        app = GardenApp(**payload)
        assert app.revision == "abc123"
        assert app.name == "hello"

    def test_garden_app_accepts_unknown_future_field(self):
        from kamiwaza_sdk.schemas.apps import GardenApp

        # An older client reading a future catalog should not crash on
        # fields it has never heard of.
        payload = {
            "name": "hello",
            "version": "1.0.0",
            "description": "test",
            "compose_yml": "services: {}",
            "future_field_we_have_not_invented_yet": {"nested": True},
        }
        # Should not raise
        GardenApp(**payload)


@pytest.mark.unit
class TestRegistryBuilderRevision:
    def test_build_entry_includes_revision_when_set(self):
        from kamiwaza_extensions.registry_builder import RegistryBuilder

        builder = RegistryBuilder()
        entry = builder.build_entry(
            metadata={"name": "hello", "description": "x"},
            transformed_compose={"services": {}},
            registry="example.io",
            version="1.0.0",
            stage="prod",
            revision="abc123",
        )
        assert entry["revision"] == "abc123"

    def test_build_entry_omits_revision_when_none(self):
        from kamiwaza_extensions.registry_builder import RegistryBuilder

        builder = RegistryBuilder()
        entry = builder.build_entry(
            metadata={"name": "hello", "description": "x"},
            transformed_compose={"services": {}},
            registry="example.io",
            version="1.0.0",
            stage="prod",
        )
        assert "revision" not in entry


@pytest.mark.unit
class TestPublishRoundTripWithRevision:
    """Round-trip integration through merge_into_registry (review PR #84
    Critical #1). The dedup-only tests above don't exercise the merge
    layer; this surfaces the actual end-to-end semantics with revisions.

    Catalog model per §4.2.5: one entry per ``(name, semver)``; revision
    is a tag on that entry. So:

      * Same triple, no force → CatalogDedupError (idempotency).
      * Same triple, --force → replace (CI re-run safe).
      * Same (name, semver), different revision, no force → merge layer
        rejects with a revision-aware error message so the user knows
        which revision is being replaced.
      * Same (name, semver), different revision, --force → replace
        (CI / intentional revision bump).
    """

    def _merge(
        self,
        existing: list[dict],
        *,
        revision: str | None,
        force: bool = False,
    ) -> tuple[list[dict], str] | type[Exception]:
        """Run the full ``CatalogDedupGuard.check`` → ``merge_into_registry``
        sequence the same way ``CatalogPublisher.publish`` does."""
        from kamiwaza_extensions.registry_builder import RegistryBuilder

        new_entry: dict = {
            "name": "hello",
            "version": "1.0.0",
            "description": "x",
        }
        if revision is not None:
            new_entry["revision"] = revision
        CatalogDedupGuard().check(existing, new_entry, force=force)
        merged, action = RegistryBuilder().merge_into_registry(
            new_entry, existing, force=force,
        )
        return merged, action

    def test_first_publish_inserts(self):
        merged, action = self._merge([], revision="abc123")
        assert action == "insert"
        assert merged == [{"name": "hello", "version": "1.0.0", "description": "x", "revision": "abc123"}]

    def test_idempotent_re_publish_same_triple_rejects(self):
        existing = [{"name": "hello", "version": "1.0.0", "description": "x", "revision": "abc123"}]
        with pytest.raises(CatalogDedupError):
            self._merge(existing, revision="abc123")

    def test_idempotent_re_publish_same_triple_with_force_replaces(self):
        existing = [{"name": "hello", "version": "1.0.0", "description": "x", "revision": "abc123"}]
        merged, action = self._merge(existing, revision="abc123", force=True)
        assert action == "replace"
        assert len(merged) == 1
        assert merged[0]["revision"] == "abc123"

    def test_different_revision_same_version_no_force_rejects_with_revision_message(self):
        # The dedup guard passes (different triple) but the merge layer
        # rejects on duplicate version. The error names the existing
        # revision so the user knows what they'd be replacing.
        existing = [{"name": "hello", "version": "1.0.0", "description": "x", "revision": "rev-a"}]
        with pytest.raises(ValueError) as exc_info:
            self._merge(existing, revision="rev-b")
        msg = str(exc_info.value)
        assert "'rev-a'" in msg
        assert "'rev-b'" in msg
        assert "force=True" in msg

    def test_different_revision_same_version_with_force_replaces(self):
        existing = [{"name": "hello", "version": "1.0.0", "description": "x", "revision": "rev-a"}]
        merged, action = self._merge(existing, revision="rev-b", force=True)
        assert action == "replace"
        assert len(merged) == 1
        assert merged[0]["revision"] == "rev-b"

    def test_existing_unrevisioned_entry_clear_error_when_publishing_new_revision(self):
        # An older entry without `revision` (pre-ENG-3884 publish) — the
        # error should still mention the new publish's revision so the
        # user understands why the publish was rejected.
        existing = [{"name": "hello", "version": "1.0.0", "description": "x"}]
        with pytest.raises(ValueError) as exc_info:
            self._merge(existing, revision="rev-b")
        # Existing has no revision string to surface, but the user-facing
        # message still tells them to use --force.
        assert "force=True" in str(exc_info.value)


@pytest.mark.unit
class TestPublishRevisionFlag:
    """The ``--revision`` Typer flag must reach
    ``CatalogPublisher.publish(revision=...)`` and
    ``RegistryBuilder.build_entry(revision=...)`` (review Critical #1:
    "the new contract is unreachable from CI" without the flag wired)."""

    def test_publish_command_threads_revision_kwarg(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from kamiwaza_extensions.commands.publish import run_publish

        # Reuse fixtures from the sibling test module (cross-import via the
        # full package path; relative imports break under pytest's rootdir).
        from tests.unit.extensions.test_publish_cmd import (
            _make_extension_info,
            _make_profile,
            _make_publish_result,
            _make_validation_result,
        )

        with (
            patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher") as PubCls,
            patch("kamiwaza_extensions.registry_builder.RegistryBuilder") as RBCls,
            patch("kamiwaza_extensions.image_pusher.ImagePusher"),
            patch("kamiwaza_extensions.image_builder.ImageBuilder") as BuildCls,
            patch("kamiwaza_extensions.profile_manager.ProfileManager") as PMCls,
            patch("kamiwaza_extensions.compose_transformer.ComposeTransformer") as CTCls,
            patch("kamiwaza_extensions.validators.compose.ComposeValidator") as CVCls,
            patch("kamiwaza_extensions.validators.metadata.MetadataValidator") as MVCls,
            patch("kamiwaza_extensions.extension_detector.ExtensionDetector") as DetCls,
        ):
            DetCls.return_value.detect.return_value = _make_extension_info(tmp_path)
            MVCls.return_value.validate.return_value = _make_validation_result()
            CVCls.return_value.validate.return_value = _make_validation_result()
            PMCls.return_value.resolve_profile.return_value = _make_profile()
            CTCls.return_value.transform.return_value = {"services": {}}
            BuildCls.return_value.build.return_value = []

            rb = MagicMock()
            rb.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
            RBCls.return_value = rb

            pub = MagicMock()
            pub.publish.return_value = _make_publish_result()
            PubCls.return_value = pub

            run_publish(stage="dev", revision="ci-sha-abc123")

        # Both build_entry and publish must receive the revision kwarg.
        rb.build_entry.assert_called_once()
        assert rb.build_entry.call_args.kwargs["revision"] == "ci-sha-abc123"
        pub.publish.assert_called_once()
        assert pub.publish.call_args.kwargs["revision"] == "ci-sha-abc123"

    def test_publish_command_uses_revision_as_image_tag(self, tmp_path):
        """ENG-3591: ``--revision`` must drive the docker image tag end-to-
        end. ComposeTransformer (the canonical source of tag rewriting) and
        ImageBuilder both receive ``revision_tag=<revision>`` so the
        transformed compose carries the SHA tag and the locally-built image
        is tagged consistently."""
        from unittest.mock import MagicMock, patch

        from kamiwaza_extensions.commands.publish import run_publish

        from tests.unit.extensions.test_publish_cmd import (
            _make_extension_info,
            _make_profile,
            _make_publish_result,
            _make_validation_result,
        )

        with (
            patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher") as PubCls,
            patch("kamiwaza_extensions.registry_builder.RegistryBuilder") as RBCls,
            patch("kamiwaza_extensions.image_pusher.ImagePusher"),
            patch("kamiwaza_extensions.image_builder.ImageBuilder") as BuildCls,
            patch("kamiwaza_extensions.profile_manager.ProfileManager") as PMCls,
            patch("kamiwaza_extensions.compose_transformer.ComposeTransformer") as CTCls,
            patch("kamiwaza_extensions.validators.compose.ComposeValidator") as CVCls,
            patch("kamiwaza_extensions.validators.metadata.MetadataValidator") as MVCls,
            patch("kamiwaza_extensions.extension_detector.ExtensionDetector") as DetCls,
        ):
            DetCls.return_value.detect.return_value = _make_extension_info(tmp_path)
            MVCls.return_value.validate.return_value = _make_validation_result()
            CVCls.return_value.validate.return_value = _make_validation_result()
            PMCls.return_value.resolve_profile.return_value = _make_profile()
            CTCls.return_value.transform.return_value = {"services": {}}
            BuildCls.return_value.build.return_value = []

            rb = MagicMock()
            rb.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
            RBCls.return_value = rb

            PubCls.return_value.publish.return_value = _make_publish_result()

            run_publish(stage="dev", revision="1.0.0-dev-abc1234")

        # ComposeTransformer is the canonical tag rewriter. It must receive
        # the revision so the in-memory compose carries the SHA tag.
        assert (
            CTCls.return_value.transform.call_args.kwargs["revision_tag"]
            == "1.0.0-dev-abc1234"
        )
        # ImageBuilder uses the same tag for the locally-built image.
        assert (
            BuildCls.return_value.build.call_args.kwargs["revision_tag"]
            == "1.0.0-dev-abc1234"
        )
        # Stage semantics preserved — catalog routing unchanged.
        assert rb.build_entry.call_args.kwargs["stage"] == "dev"
        # No image_tag_override — that workaround was removed when
        # ComposeTransformer became the sole tag-rewriting authority.
        assert "image_tag_override" not in rb.build_entry.call_args.kwargs

    def test_publish_command_dry_run_uses_revision_as_image_tag(self, tmp_path):
        """``--dry-run`` exercises the same ComposeTransformer call as the
        real path; the revision must reach ComposeTransformer so the
        previewed compose mirrors what a real publish would write."""
        from unittest.mock import MagicMock, patch

        from kamiwaza_extensions.commands.publish import run_publish

        from tests.unit.extensions.test_publish_cmd import (
            _make_extension_info,
            _make_profile,
            _make_publish_result,
            _make_validation_result,
        )

        with (
            patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher") as PubCls,
            patch("kamiwaza_extensions.registry_builder.RegistryBuilder") as RBCls,
            patch("kamiwaza_extensions.image_pusher.ImagePusher") as PusherCls,
            patch("kamiwaza_extensions.image_builder.ImageBuilder") as BuildCls,
            patch("kamiwaza_extensions.profile_manager.ProfileManager") as PMCls,
            patch("kamiwaza_extensions.compose_transformer.ComposeTransformer") as CTCls,
            patch("kamiwaza_extensions.validators.compose.ComposeValidator") as CVCls,
            patch("kamiwaza_extensions.validators.metadata.MetadataValidator") as MVCls,
            patch("kamiwaza_extensions.extension_detector.ExtensionDetector") as DetCls,
        ):
            DetCls.return_value.detect.return_value = _make_extension_info(tmp_path)
            MVCls.return_value.validate.return_value = _make_validation_result()
            CVCls.return_value.validate.return_value = _make_validation_result()
            PMCls.return_value.resolve_profile.return_value = _make_profile()
            CTCls.return_value.transform.return_value = {"services": {}}

            rb = MagicMock()
            rb.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
            RBCls.return_value = rb

            PubCls.return_value.publish.return_value = _make_publish_result()

            run_publish(stage="dev", revision="1.0.0-dev-abc1234", dry_run=True)

        # Same revision must reach ComposeTransformer in the dry-run path.
        assert (
            CTCls.return_value.transform.call_args.kwargs["revision_tag"]
            == "1.0.0-dev-abc1234"
        )
        # No build or push side effects in dry-run.
        BuildCls.return_value.build.assert_not_called()
        PusherCls.return_value.push.assert_not_called()

    def test_publish_command_no_revision_uses_stage_default_image_tag(
        self, tmp_path,
    ):
        """Without ``--revision`` ComposeTransformer receives the
        stage-derived default tag (``{version}-{stage}`` for non-prod)."""
        from unittest.mock import MagicMock, patch

        from kamiwaza_extensions.commands.publish import run_publish

        from tests.unit.extensions.test_publish_cmd import (
            _make_extension_info,
            _make_profile,
            _make_publish_result,
            _make_validation_result,
        )

        with (
            patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher") as PubCls,
            patch("kamiwaza_extensions.registry_builder.RegistryBuilder") as RBCls,
            patch("kamiwaza_extensions.image_pusher.ImagePusher"),
            patch("kamiwaza_extensions.image_builder.ImageBuilder") as BuildCls,
            patch("kamiwaza_extensions.profile_manager.ProfileManager") as PMCls,
            patch("kamiwaza_extensions.compose_transformer.ComposeTransformer") as CTCls,
            patch("kamiwaza_extensions.validators.compose.ComposeValidator") as CVCls,
            patch("kamiwaza_extensions.validators.metadata.MetadataValidator") as MVCls,
            patch("kamiwaza_extensions.extension_detector.ExtensionDetector") as DetCls,
        ):
            DetCls.return_value.detect.return_value = _make_extension_info(tmp_path)
            MVCls.return_value.validate.return_value = _make_validation_result()
            CVCls.return_value.validate.return_value = _make_validation_result()
            PMCls.return_value.resolve_profile.return_value = _make_profile()
            CTCls.return_value.transform.return_value = {"services": {}}
            BuildCls.return_value.build.return_value = []

            rb = MagicMock()
            rb.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
            RBCls.return_value = rb

            PubCls.return_value.publish.return_value = _make_publish_result()

            run_publish(stage="dev")

        assert (
            CTCls.return_value.transform.call_args.kwargs["revision_tag"]
            == "1.0.0-dev"
        )


@pytest.mark.unit
class TestPublishRevisionGrammarValidatedEarly:
    """Review re-review PR #84 M2: bad ``--revision`` input must reject
    *before* the build/push side effects so an invalid revision can't
    leak orphan tags into the registry. The grammar check used to live
    inside ``CatalogPublisher.publish()`` (after step 5/6) and so a
    revision like ``foo/bar`` would build + push the image first, then
    fail at the publish step."""

    def _run_publish_with_invalid_revision(self, revision: str, tmp_path):
        from unittest.mock import MagicMock, patch

        import typer

        from kamiwaza_extensions.commands.publish import run_publish

        from tests.unit.extensions.test_publish_cmd import (
            _make_extension_info,
            _make_profile,
            _make_validation_result,
        )

        with (
            patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher") as PubCls,
            patch("kamiwaza_extensions.registry_builder.RegistryBuilder") as RBCls,
            patch("kamiwaza_extensions.image_pusher.ImagePusher") as PusherCls,
            patch("kamiwaza_extensions.image_builder.ImageBuilder") as BuildCls,
            patch("kamiwaza_extensions.profile_manager.ProfileManager") as PMCls,
            patch("kamiwaza_extensions.compose_transformer.ComposeTransformer") as CTCls,
            patch("kamiwaza_extensions.validators.compose.ComposeValidator") as CVCls,
            patch("kamiwaza_extensions.validators.metadata.MetadataValidator") as MVCls,
            patch("kamiwaza_extensions.extension_detector.ExtensionDetector") as DetCls,
        ):
            DetCls.return_value.detect.return_value = _make_extension_info(tmp_path)
            MVCls.return_value.validate.return_value = _make_validation_result()
            CVCls.return_value.validate.return_value = _make_validation_result()
            PMCls.return_value.resolve_profile.return_value = _make_profile()
            CTCls.return_value.transform.return_value = {"services": {}}
            BuildCls.return_value.build.return_value = []
            RBCls.return_value.build_entry.return_value = {"name": "x", "version": "1.0.0"}
            PubCls.return_value.publish.return_value = MagicMock()

            try:
                # `typer.Exit` is the public name; under the hood it's
                # `click.exceptions.Exit`. Match either so the test isn't
                # tied to typer's wrapping detail.
                with pytest.raises(typer.Exit) as exc_info:
                    run_publish(stage="dev", revision=revision)
            finally:
                build_calls = BuildCls.return_value.build.call_count
                push_calls = PusherCls.return_value.push.call_count
                publish_calls = PubCls.return_value.publish.call_count

        return exc_info.value.exit_code, build_calls, push_calls, publish_calls

    @pytest.mark.parametrize(
        "bad_revision", ["foo/bar", "BAD CASE", "with space", "-leading-dash"],
    )
    def test_invalid_revision_rejects_before_build_or_push(self, bad_revision, tmp_path):
        from unittest.mock import MagicMock  # noqa: F401 — used inside helper

        rc, build_calls, push_calls, publish_calls = self._run_publish_with_invalid_revision(
            bad_revision, tmp_path,
        )

        from kamiwaza_extensions.exit_codes import ExitCode

        # Validation exit code (2), not generic 1.
        assert rc == int(ExitCode.VALIDATION)

        # And critically — none of the side-effecting steps ran.
        assert build_calls == 0, (
            f"image build was invoked despite invalid revision {bad_revision!r}"
        )
        assert push_calls == 0, (
            f"image push was invoked despite invalid revision {bad_revision!r} — "
            "an orphan tag would be left in the registry"
        )
        assert publish_calls == 0
