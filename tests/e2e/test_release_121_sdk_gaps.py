"""Guard the 1.2.1 SDK producer backlog against silent row loss."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests/e2e/release_121_sdk_gaps.yaml"
EXPECTED = {
    "apps.app-garden",
    "auth.enterprise-sso",
    "authz.rebac-access-control",
    "connectors.managed-data-sources",
    "connectors.workroom-surface-access",
    "context.workroom-document-search",
    "delegation.brokered-resource-access",
    "delegation.capability-grants",
    "delegation.consent-and-approval-gates",
    "delegation.run-claims-lifecycle",
    "delegation.workload-identity-and-attestation",
    "extensions.developer-path",
    "federation.cluster-pairing",
    "federation.execution-gates",
    "federation.federated-retrieval",
    "federation.remote-jobs",
    "ingestion.scheduled-ingest",
    "kaizen.custom-agents",
    "models.discover-and-download",
    "ontology.workroom-knowledge-graph",
    "platform.app-generation-path",
    "platform.usage-and-audit",
    "tools.mcp-tool-shed",
    "workrooms.admin-oversight",
    "workrooms.app-launch",
    "workrooms.archive-and-delete",
    "workrooms.export-bundle",
    "workrooms.session-scoping",
}


def test_release_121_sdk_gap_manifest_is_complete() -> None:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["release"] == "1.2.1"
    entries = data["capabilities"]
    assert len(entries) == len(EXPECTED)
    ids = [entry["id"] for entry in entries]
    assert set(ids) == EXPECTED
    assert len(ids) == len(set(ids))
    for entry in entries:
        assert entry["state"] in {
            "fixture-blocked",
            "rig-blocked",
            "product-blocked",
            "product-failed",
            "partial",
            "producer-missing",
            "live-passed",
        }
        assert entry["next"].strip()
        if probe := entry.get("probe"):
            path, *symbols = probe.split("::")
            source = ROOT / path
            assert source.is_file(), f"Missing probe source for {entry['id']}: {source}"
            nodes = ast.parse(source.read_text(encoding="utf-8")).body
            for symbol in symbols:
                match = next(
                    (
                        node
                        for node in nodes
                        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                        and node.name == symbol
                    ),
                    None,
                )
                assert (
                    match is not None
                ), f"Missing probe symbol for {entry['id']}: {symbol}"
                nodes = match.body
