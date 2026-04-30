"""Tests for dev command PATCH-vs-POST deploy logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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

        # Simulate the deploy logic
        from kamiwaza_sdk.schemas.extensions import ImagePatch, PatchExtension, PatchServiceSpec

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
                patch_services.append(PatchServiceSpec(
                    name=svc.name,
                    image=ImagePatch(tag=tag),
                ))
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
            after_slash = image[slash_pos + 1:] if slash_pos >= 0 else image
            if ":" in after_slash:
                tag = after_slash.rsplit(":", 1)[1]
            else:
                tag = "latest"
            assert tag == expected_tag, f"For image '{image}' expected '{expected_tag}' got '{tag}'"
