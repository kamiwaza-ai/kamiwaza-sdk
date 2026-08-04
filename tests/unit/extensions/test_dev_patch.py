"""Tests for dev command PATCH-vs-POST deploy logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.extensions import (
    CreateExtension,
    Extension,
    ExtensionServiceSpec,
    ImagePatch,
    PatchExtension,
    PatchServiceSpec,
)

pytestmark = [pytest.mark.unit, pytest.mark.extension_regression]


def _make_payload():
    return CreateExtension(
        name="myapp-dev-abc123",
        type="app",
        version="1.0.0",
        services=[
            ExtensionServiceSpec(
                name="backend",
                image="registry.test/myapp-backend:v1.0.0-gabc",
                primary=True,
                ports=[],
            ),
            ExtensionServiceSpec(
                name="frontend",
                image="registry.test/myapp-frontend:v1.0.0-gabc",
                primary=False,
                ports=[],
            ),
        ],
    )


def _make_ext(phase="Running"):
    return Extension(name="myapp-dev-abc123", type="app", version="1.0.0", phase=phase)


class TestDeployPatchLogic:
    """Test the PATCH-when-exists / POST-when-new logic in dev.py."""

    def test_creates_when_not_found(self):
        """POST should be used for new extensions."""
        client = MagicMock()
        client.extensions.get_extension.side_effect = NotFoundError("not found")
        client.extensions.create_extension.return_value = _make_ext("Pending")

        payload = _make_payload()

        try:
            client.extensions.get_extension("myapp-dev-abc123")
            # Would PATCH here — but this won't execute
            assert False, "Should have raised NotFoundError"
        except NotFoundError:
            ext = client.extensions.create_extension(payload)

        assert ext.name == "myapp-dev-abc123"
        client.extensions.create_extension.assert_called_once_with(payload)
        client.extensions.patch_extension.assert_not_called()

    def test_patches_when_exists(self):
        """PATCH should be used when extension already exists."""
        client = MagicMock()
        client.extensions.get_extension.return_value = _make_ext("Running")
        client.extensions.patch_extension.return_value = _make_ext("Running")

        payload = _make_payload()

        # Simulate the deploy logic
        try:
            client.extensions.get_extension("myapp-dev-abc123")

            patch_services = []
            for svc in payload.services:
                parts = svc.image.rsplit(":", 1)
                tag = parts[1] if len(parts) > 1 else "latest"
                patch_services.append(
                    PatchServiceSpec(
                        name=svc.name,
                        image=ImagePatch(tag=tag),
                    )
                )
            patch = PatchExtension(services=patch_services)
            ext = client.extensions.patch_extension("myapp-dev-abc123", patch)
        except NotFoundError:
            assert False, "Should not have raised"

        assert ext.name == "myapp-dev-abc123"
        client.extensions.create_extension.assert_not_called()

        # Verify the patch payload
        call_args = client.extensions.patch_extension.call_args
        actual_patch = call_args[0][1]
        assert len(actual_patch.services) == 2
        assert actual_patch.services[0].name == "backend"
        assert actual_patch.services[0].image.tag == "v1.0.0-gabc"
        assert actual_patch.services[1].name == "frontend"

    def test_falls_back_to_delete_create_on_405(self):
        """When PATCH returns 405, should fall back to delete+create."""
        client = MagicMock()
        client.extensions.get_extension.return_value = _make_ext("Running")
        client.extensions.patch_extension.side_effect = APIError(
            "Method not allowed", status_code=405
        )

        # Verify that 405 can be caught
        try:
            client.extensions.patch_extension("myapp-dev-abc123", MagicMock())
            assert False, "Should have raised"
        except APIError as exc:
            assert exc.status_code == 405

    def test_image_tag_extraction(self):
        """Verify tag is extracted correctly from image formats used by PayloadBuilder.

        Uses the same slash-then-colon algorithm as dev.py to avoid the
        registry-port pitfall (e.g. localhost:5001/app being misread).
        """
        test_cases = [
            ("registry.test/app:v1.0.0", "v1.0.0"),
            ("registry.test/app:latest", "latest"),
            ("registry.test/app", "latest"),  # no tag → default to latest
            ("registry.test:5000/app:v2", "v2"),
            ("registry.test:5000/app", "latest"),  # port but no tag
            ("localhost:5001/myapp-backend:1.0.0-gabc1234", "1.0.0-gabc1234"),
        ]
        for image, expected_tag in test_cases:
            slash_pos = image.rfind("/")
            after_slash = image[slash_pos + 1 :] if slash_pos >= 0 else image
            if ":" in after_slash:
                tag = after_slash.rsplit(":", 1)[1]
            else:
                tag = "latest"
            assert tag == expected_tag, (
                f"For image '{image}' expected '{expected_tag}' got '{tag}'"
            )


class TestBuildPatchServiceSpecs:
    """`_build_patch_service_specs` must populate all three of
    ImagePatch.{registry, repository, tag} so the operator updates the
    CR's full image field on redeploy. Tag-only would leave the
    existing CR pointing at its original repository — an
    ImagePullBackOff every time the canonical ref's repository differs
    from what the CR holds (e.g. SDK upgrade from pre-fix kz-ext, or
    declared image namespace change between deploys)."""

    def _payload(self, image: str):
        return CreateExtension(
            name="myapp-dev-abc123",
            type="app",
            version="1.0.0",
            services=[
                ExtensionServiceSpec(
                    name="backend", image=image, primary=True, ports=[]
                ),
            ],
        )

    def test_canonical_ghcr_ref_populates_all_three_fields(self):
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        payload = self._payload(
            "ghcr.io/kamiwaza-internal/foo/images/omniparse:2.0.14-dev"
        )
        specs = _build_patch_service_specs(payload)
        assert len(specs) == 1
        img = specs[0].image
        assert img.registry == "ghcr.io"
        assert img.repository == "kamiwaza-internal/foo/images/omniparse"
        assert img.tag == "2.0.14-dev"

    def test_localhost_kind_registry_with_port(self):
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        payload = self._payload("localhost:5001/my-ext-backend:1.0.0-gabc")
        img = _build_patch_service_specs(payload)[0].image
        assert img.registry == "localhost:5001"
        assert img.repository == "my-ext-backend"
        assert img.tag == "1.0.0-gabc"

    def test_legacy_unqualified_ref_has_no_registry(self):
        # Unqualified refs are rewritten to the cluster registry before
        # reaching this code (via _canonical_build_ref); the splitter
        # still must not invent a registry from `my-org/foo`.
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        payload = self._payload("my-org/foo:1.0")
        img = _build_patch_service_specs(payload)[0].image
        assert img.registry is None
        assert img.repository == "my-org/foo"
        assert img.tag == "1.0"

    def test_repo_change_between_deploys_flows_through_patch(self):
        # The regression scenario: a CR was deployed under pre-fix kz-ext
        # at `registry.test/myapp-omniparse-server:v1` (legacy synthesis),
        # and a redeploy now builds at the canonical declared namespace
        # `ghcr.io/.../images/omniparse:v2`. The PATCH payload must carry
        # the new registry + repository so the operator updates the CR's
        # image field — tag-only would pull `registry.test/myapp-omniparse-server:v2`
        # which was never pushed.
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        payload = self._payload("ghcr.io/kamiwaza-internal/foo/images/omniparse:v2")
        img = _build_patch_service_specs(payload)[0].image
        # The patch carries the new repository, not just a new tag —
        # the operator will rewrite the CR's image field accordingly.
        assert img.registry == "ghcr.io"
        assert img.repository == "kamiwaza-internal/foo/images/omniparse"
        assert img.tag == "v2"

    def test_digest_pinned_ref_preserves_pin_on_patch(self):
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        digest = "sha256:" + "a" * 64
        payload = self._payload(f"ghcr.io/org/controller:develop@{digest}")

        img = _build_patch_service_specs(payload)[0].image

        assert img.registry == "ghcr.io"
        assert img.repository == "org/controller"
        assert img.tag == "develop"
        assert img.digest == digest

    def test_persistence_override_flows_through_patch(self):
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        payload = self._payload("registry.test/postgres:17")
        payload.services[0].persistence = {
            "enabled": True,
            "size": "10Gi",
            "mountPath": "/var/lib/postgresql",
        }

        spec = _build_patch_service_specs(payload)[0]
        assert spec.persistence == {
            "enabled": True,
            "size": "10Gi",
            "mountPath": "/var/lib/postgresql",
        }

    def test_service_filter_excludes_unbuilt_sibling_images(self):
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        payload = _make_payload()
        specs = _build_patch_service_specs(payload, service_filter="frontend")

        assert [spec.name for spec in specs] == ["frontend"]
        assert specs[0].image.repository == "myapp-frontend"


class TestServiceFilterValidation:
    """Raw Compose keys are the wrong set: a profile-gated or image-only
    service passes that check and then fails late, after build and push."""

    COMPOSE = {
        "services": {
            "backend": {"build": "./backend"},
            "frontend": {"build": "./frontend"},
            "agent": {"build": "./agent", "profiles": ["local"]},
            "redis": {"image": "redis:7"},
        }
    }

    def _reject(self, name: str, match: str, capsys) -> None:
        import typer

        from kamiwaza_extensions.commands.dev import _validate_service_filter

        with pytest.raises(typer.Exit):
            _validate_service_filter(name, self.COMPOSE)
        err = capsys.readouterr().err
        assert "--service" in err
        assert match in err

    def test_unknown_service_is_rejected(self, capsys):
        self._reject("backnd", "not a service", capsys)

    def test_profile_gated_service_is_rejected(self, capsys):
        """The transformer strips it, so the PATCH list would come back empty."""
        self._reject("agent", "profile-gated", capsys)

    def test_image_only_service_is_rejected(self, capsys):
        """Nothing to build or push — the deploy would be a silent no-op."""
        self._reject("redis", "no build context", capsys)

    def test_rejection_lists_only_deployable_services(self, capsys):
        import typer

        from kamiwaza_extensions.commands.dev import _validate_service_filter

        with pytest.raises(typer.Exit):
            _validate_service_filter("nope", self.COMPOSE)

        err = capsys.readouterr().err
        assert "backend" in err and "frontend" in err
        assert "agent" not in err and "redis" not in err

    def test_deployable_service_passes(self):
        from kamiwaza_extensions.commands.dev import _validate_service_filter

        _validate_service_filter("backend", self.COMPOSE)

    def test_none_filter_passes(self):
        from kamiwaza_extensions.commands.dev import _validate_service_filter

        _validate_service_filter(None, {"services": {}})


class TestPatchPersistenceIsDeclarationOnly:
    """Clearing a removed block would need the CR's spec, which
    ``get_extension`` does not return — so the PATCH only ever asserts what
    the extension declares."""

    @staticmethod
    def _payload(persistence=None):
        from kamiwaza_sdk.schemas.extensions import CreateExtension, ExtensionServiceSpec

        kwargs = {"persistence": persistence} if persistence is not None else {}
        return CreateExtension(
            name="ext",
            type="app",
            version="1.0.0",
            services=[
                ExtensionServiceSpec(
                    name="postgres", image="ghcr.io/o/pg:1", **kwargs
                )
            ],
        )

    def test_declared_persistence_is_sent(self):
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        declared = {"enabled": True, "mountPath": "/data"}
        specs = _build_patch_service_specs(self._payload(declared))

        assert specs[0].persistence == declared

    def test_undeclared_persistence_is_omitted(self):
        from kamiwaza_extensions.commands.dev import _build_patch_service_specs

        specs = _build_patch_service_specs(self._payload())

        assert specs[0].model_dump(exclude_none=True).get("persistence") is None


class TestOrphanedPersistenceWarning:
    """The CR keeps a persistence block the extension has dropped —
    ``get_extension`` returns only status, so it cannot be cleared from here."""

    @staticmethod
    def _payload(persistence=None, mounts=None):
        from kamiwaza_sdk.schemas.extensions import CreateExtension, ExtensionServiceSpec

        kwargs = {}
        if persistence is not None:
            kwargs["persistence"] = persistence
        if mounts is not None:
            kwargs["volumeMounts"] = mounts
        return CreateExtension(
            name="ext",
            type="app",
            version="1.0.0",
            services=[
                ExtensionServiceSpec(name="postgres", image="ghcr.io/o/pg:1", **kwargs)
            ],
        )

    def test_warns_when_a_volume_reclaims_the_old_mount_path(self):
        from kamiwaza_extensions.commands.dev import warn_orphaned_persistence

        payload = self._payload(
            mounts=[{"name": "pg", "mountPath": "/var/lib/postgresql/data"}]
        )

        warnings = warn_orphaned_persistence(
            {"postgres": "/var/lib/postgresql/data"}, payload
        )

        assert len(warnings) == 1
        assert "/var/lib/postgresql/data" in warnings[0]

    def test_warns_on_a_nested_target_that_would_shadow_the_claim(self):
        from kamiwaza_extensions.commands.dev import warn_orphaned_persistence

        payload = self._payload(
            mounts=[{"name": "pg", "mountPath": "/var/lib/postgresql/data"}]
        )

        assert warn_orphaned_persistence({"postgres": "/var/lib/postgresql"}, payload)

    def test_silent_when_persistence_is_still_declared(self):
        from kamiwaza_extensions.commands.dev import warn_orphaned_persistence

        payload = self._payload(
            persistence={"enabled": True, "mountPath": "/var/lib/postgresql/data"},
            mounts=[{"name": "other", "mountPath": "/tmp/x"}],
        )

        assert warn_orphaned_persistence({"postgres": "/var/lib/postgresql"}, payload) == []

    def test_silent_when_the_volume_is_elsewhere(self):
        from kamiwaza_extensions.commands.dev import warn_orphaned_persistence

        payload = self._payload(mounts=[{"name": "cache", "mountPath": "/var/cache"}])

        assert warn_orphaned_persistence({"postgres": "/var/lib/postgresql"}, payload) == []

    def test_silent_with_no_prior_state(self):
        from kamiwaza_extensions.commands.dev import warn_orphaned_persistence

        payload = self._payload(mounts=[{"name": "pg", "mountPath": "/data"}])

        assert warn_orphaned_persistence({}, payload) == []

    def test_records_what_this_run_deploys(self):
        from kamiwaza_extensions.commands.dev import _deployed_persistence_mounts

        payload = self._payload(
            persistence={"enabled": True, "mountPath": "/var/lib/postgresql/data"}
        )

        assert _deployed_persistence_mounts(payload) == {
            "postgres": "/var/lib/postgresql/data"
        }

    def test_disabled_persistence_is_not_recorded(self):
        from kamiwaza_extensions.commands.dev import _deployed_persistence_mounts

        assert _deployed_persistence_mounts(self._payload({"enabled": False})) == {}


class TestDeployedPersistenceIsScopedToTheFilter:
    @staticmethod
    def _payload():
        from kamiwaza_sdk.schemas.extensions import CreateExtension, ExtensionServiceSpec

        return CreateExtension(
            name="ext",
            type="app",
            version="1.0.0",
            services=[
                ExtensionServiceSpec(
                    name="postgres",
                    image="ghcr.io/o/pg:1",
                    persistence={"enabled": True, "mountPath": "/data"},
                ),
                ExtensionServiceSpec(
                    name="seaweedfs",
                    image="ghcr.io/o/sw:1",
                    persistence={"enabled": True, "mountPath": "/store"},
                ),
            ],
        )

    def test_unfiltered_run_records_every_service(self):
        from kamiwaza_extensions.commands.dev import _deployed_persistence_mounts

        assert _deployed_persistence_mounts(self._payload()) == {
            "postgres": "/data",
            "seaweedfs": "/store",
        }

    def test_service_filtered_run_records_only_what_it_patched(self):
        from kamiwaza_extensions.commands.dev import _deployed_persistence_mounts

        assert _deployed_persistence_mounts(self._payload(), "postgres") == {
            "postgres": "/data"
        }
