"""Checks that qualification fixtures faithfully encode the intended release."""

import importlib.util
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location(
    "catalog_harness", Path(__file__).with_name("catalog_harness.py")
)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "endpoint",
    ["https://example.com", "https://r2.cloudflarestorage.com", "http://10.77.0.1"],
)
def test_publisher_refuses_non_loopback_before_loading_credentials(endpoint):
    with pytest.raises(ValueError, match="loopback"):
        harness.publisher(endpoint)


@pytest.mark.parametrize("kind", ["app", "service", "tool"])
def test_version_payload_and_metadata_survive_real_registry_builder(kind):
    row = harness.entry(kind, "0.4.0", "1.3.1")
    assert row["kamiwaza_version"] == ">=1.3.1"
    assert row["qualification_metadata"] == {"preserve": True, "minimum": "1.3.1"}
    compose = yaml.safe_load(row["compose_yml"])
    web = compose["services"]["web"]
    assert "@sha256:" in web["image"]
    assert "'version': '0.4.0'" in web["command"][3]
    assert row["docker_images"] == [web["image"]]
