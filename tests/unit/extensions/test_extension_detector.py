"""Tests for ExtensionDetector."""

import json

import pytest
import yaml

from kamiwaza_extensions.extension_detector import (
    ExtensionDetector,
    ExtensionNotFoundError,
    MultipleExtensionsError,
)


@pytest.fixture
def detector():
    return ExtensionDetector()


@pytest.fixture
def ext_dir(tmp_path):
    """Create a minimal extension directory."""
    meta = {"name": "my-app", "version": "1.0.0", "source_type": "kamiwaza"}
    (tmp_path / "kamiwaza.json").write_text(json.dumps(meta))
    compose = {"services": {"backend": {"build": "./backend", "ports": ["8000"]}}}
    (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))
    return tmp_path


class TestFindRoot:
    def test_root_level(self, detector, ext_dir):
        info = detector.detect(ext_dir)
        assert info.path == ext_dir
        assert info.name == "my-app"
        assert info.version == "1.0.0"

    def test_one_level_deep(self, detector, tmp_path):
        sub = tmp_path / "my-ext"
        sub.mkdir()
        meta = {"name": "nested-ext", "version": "2.0.0"}
        (sub / "kamiwaza.json").write_text(json.dumps(meta))
        (sub / "docker-compose.yml").write_text("services: {}")

        info = detector.detect(tmp_path)
        assert info.path == sub
        assert info.name == "nested-ext"

    def test_multiple_extensions_error(self, detector, tmp_path):
        for name in ("ext-a", "ext-b"):
            d = tmp_path / name
            d.mkdir()
            (d / "kamiwaza.json").write_text(json.dumps({"name": name}))

        with pytest.raises(MultipleExtensionsError, match="Multiple kamiwaza.json"):
            detector.detect(tmp_path)

    def test_no_extension_found(self, detector, tmp_path):
        with pytest.raises(ExtensionNotFoundError, match="No kamiwaza.json"):
            detector.detect(tmp_path)


class TestMetadataLoading:
    def test_name_fallback_to_dir(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({"version": "1.0.0"}))
        info = detector.detect(tmp_path)
        assert info.name == tmp_path.name

    def test_version_fallback(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({"name": "x"}))
        info = detector.detect(tmp_path)
        assert info.version == "0.0.0"

    def test_corrupt_json(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text("{invalid json")
        with pytest.raises(ExtensionNotFoundError, match="Cannot read"):
            detector.detect(tmp_path)

    def test_image_basename_absent_is_none(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(
            json.dumps({"name": "x", "version": "1.0.0"})
        )
        info = detector.detect(tmp_path)
        assert info.image_basename is None

    def test_image_basename_present_is_loaded(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({
            "name": "workroom-manager",
            "version": "0.13.0",
            "image_basename": "outcome-d563-workroom-manager",
        }))
        info = detector.detect(tmp_path)
        assert info.image_basename == "outcome-d563-workroom-manager"

    def test_image_basename_empty_string_is_none(self, detector, tmp_path):
        # An empty/whitespace override would synthesize bad refs like
        # `registry/-svc:tag`; normalize to None so the legacy fallback
        # (extension_name) is used.
        (tmp_path / "kamiwaza.json").write_text(json.dumps({
            "name": "x", "version": "1.0.0", "image_basename": "   ",
        }))
        info = detector.detect(tmp_path)
        assert info.image_basename is None


class TestComposeLoading:
    def test_compose_loaded(self, detector, ext_dir):
        info = detector.detect(ext_dir)
        assert info.compose_path == ext_dir / "docker-compose.yml"
        assert "services" in info.compose_data

    def test_no_compose(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({"name": "x"}))
        info = detector.detect(tmp_path)
        assert info.compose_path is None
        assert info.compose_data is None

    def test_compose_yaml_variant(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({"name": "x"}))
        (tmp_path / "compose.yml").write_text("services:\n  web:\n    image: nginx")
        info = detector.detect(tmp_path)
        assert info.compose_path == tmp_path / "compose.yml"

    def test_prefers_docker_compose_yml(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({"name": "x"}))
        (tmp_path / "docker-compose.yml").write_text("services: {a: {}}")
        (tmp_path / "compose.yml").write_text("services: {b: {}}")
        info = detector.detect(tmp_path)
        assert info.compose_path.name == "docker-compose.yml"


class TestMonorepoDiscovery:
    """ExtensionDetector should find kamiwaza.json under apps/<x>, tools/<x>, etc."""

    @pytest.mark.parametrize(
        "subdir",
        ["apps/my-ext", "tools/my-tool", "services/api", "packages/pkg", "extensions/ext"],
    )
    def test_finds_in_monorepo_subdir(self, detector, tmp_path, subdir):
        target = tmp_path / subdir
        target.mkdir(parents=True)
        (target / "kamiwaza.json").write_text(json.dumps({"name": target.name}))

        info = detector.detect(tmp_path)

        assert info.path == target
        assert info.name == target.name

    def test_ambiguous_across_monorepo_dirs_raises(self, detector, tmp_path):
        for sub in ("apps/foo", "tools/bar"):
            d = tmp_path / sub
            d.mkdir(parents=True)
            (d / "kamiwaza.json").write_text(json.dumps({"name": d.name}))

        with pytest.raises(MultipleExtensionsError) as exc:
            detector.detect(tmp_path)

        assert "apps/foo" in str(exc.value)
        assert "tools/bar" in str(exc.value)

    def test_root_kamiwaza_json_wins_over_monorepo(self, detector, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({"name": "root"}))
        (tmp_path / "apps").mkdir()
        (tmp_path / "apps" / "decoy").mkdir()
        (tmp_path / "apps" / "decoy" / "kamiwaza.json").write_text(json.dumps({"name": "decoy"}))

        info = detector.detect(tmp_path)

        assert info.path == tmp_path
        assert info.name == "root"
