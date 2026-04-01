"""Tests for ImageBuilder."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.image_builder import ImageBuilder, ImageBuildError


@pytest.fixture
def builder():
    return ImageBuilder()


@pytest.fixture
def compose_with_build():
    return {
        "services": {
            "backend": {
                "build": {"context": ".", "dockerfile": "backend/Dockerfile"},
                "ports": ["8000"],
            },
            "frontend": {
                "build": "./frontend",
                "ports": ["3000"],
            },
            "db": {
                "image": "postgres:15",
            },
        },
    }


class TestBuild:
    @patch("kamiwaza_extensions.image_builder.subprocess.run")
    def test_builds_services_with_build_context(self, mock_run, builder, compose_with_build, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        refs = builder.build(
            extension_dir=tmp_path,
            compose_data=compose_with_build,
            extension_name="my-app",
            revision_tag="1.0.0-dev-abc.123",
            registry="registry.test",
        )
        assert len(refs) == 2  # backend + frontend (not db)
        assert "registry.test/my-app-backend:1.0.0-dev-abc.123" in refs
        assert "registry.test/my-app-frontend:1.0.0-dev-abc.123" in refs

    @patch("kamiwaza_extensions.image_builder.subprocess.run")
    def test_service_filter(self, mock_run, builder, compose_with_build, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        refs = builder.build(
            extension_dir=tmp_path,
            compose_data=compose_with_build,
            extension_name="my-app",
            revision_tag="v1",
            registry="reg",
            service_filter="backend",
        )
        assert len(refs) == 1
        assert "my-app-backend" in refs[0]

    def test_service_filter_not_found(self, builder, compose_with_build, tmp_path):
        with pytest.raises(ImageBuildError, match="not found"):
            builder.build(
                extension_dir=tmp_path,
                compose_data=compose_with_build,
                extension_name="my-app",
                revision_tag="v1",
                registry="reg",
                service_filter="nonexistent",
            )

    @patch("kamiwaza_extensions.image_builder.subprocess.run")
    def test_skips_services_without_build(self, mock_run, builder, tmp_path):
        compose = {"services": {"db": {"image": "postgres:15"}}}
        mock_run.return_value = MagicMock(returncode=0)
        refs = builder.build(tmp_path, compose, "test", "v1", "reg")
        assert refs == []

    @patch("kamiwaza_extensions.image_builder.subprocess.run")
    def test_build_failure_raises(self, mock_run, builder, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="error output", stderr="")
        compose = {"services": {"api": {"build": "."}}}
        with pytest.raises(ImageBuildError, match="build failed"):
            builder.build(tmp_path, compose, "test", "v1", "reg")


class TestResolveBuildConfig:
    def test_string_build(self, builder, tmp_path):
        df, ctx = builder._resolve_build_config("./backend", tmp_path)
        assert ctx == tmp_path / "backend"
        assert df == tmp_path / "backend" / "Dockerfile"

    def test_dict_build(self, builder, tmp_path):
        df, ctx = builder._resolve_build_config(
            {"context": ".", "dockerfile": "backend/Dockerfile"}, tmp_path
        )
        assert ctx == tmp_path
        assert df == tmp_path / "backend" / "Dockerfile"
