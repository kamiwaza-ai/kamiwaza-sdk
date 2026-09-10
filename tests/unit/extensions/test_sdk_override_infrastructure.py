"""Regression coverage for SDK overrides on built infrastructure images."""

import pytest

from kamiwaza_extensions.sdk_override import (
    SdkOverrideSpec,
    generate_build_overrides,
    generate_compose_override,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "base_image",
    (
        "cgr.dev/kamiwaza/postgres:17-dev",
        "cgr.dev/chainguard/valkey:latest",
        "cgr.dev/kamiwaza/seaweedfs:4.40-dev",
        "cgr.dev/kamiwaza/jre:openjdk-21-dev",
    ),
)
def test_skips_built_infrastructure_without_sdk_runtime(tmp_path, base_image):
    """An infrastructure build context must not imply a Python runtime."""
    extension_dir = tmp_path / "extension"
    service_dir = extension_dir / "service"
    service_dir.mkdir(parents=True)
    (service_dir / "Dockerfile").write_text(f"FROM {base_image}\n")
    compose = {
        "services": {
            # Explicit runtime evidence wins over a backend-looking name.
            "backend": {"build": {"context": "./service"}},
        }
    }
    spec = SdkOverrideSpec(sdk_repo=tmp_path)

    assert generate_build_overrides(spec, compose, extension_dir=extension_dir) == []
    assert generate_compose_override(spec, compose, extension_dir=extension_dir) == {
        "services": {}
    }
