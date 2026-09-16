"""Guard against crediting connector management from dataset-spec registration."""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
MAP_PATH = Path(__file__).with_name("capability_map.yaml")


def test_dataset_spec_registration_does_not_credit_managed_connectors() -> None:
    entries = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
    mismatched = [
        entry["pattern"]
        for entry in entries
        if "connectors.managed-data-sources" in entry["capability_ids"]
        and "test_connector_spec_live.py" in entry["pattern"]
    ]
    assert not mismatched, (
        "register-from-spec returns a catalog dataset URN, not a managed "
        f"/connectors lifecycle: {mismatched}"
    )
