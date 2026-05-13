"""T5.6 — DatasetsAPI on the canonical kamiwaza_sdk surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Customer-facing surface for
catalog datasets + attribute-gate binding per design §4.2.11:

    kz.datasets.create(name, platform, **kwargs) -> str  (URN per H4 fix)
    kz.datasets.get(urn)                          -> DatasetRef
    kz.datasets.delete(urn)                       -> None
    kz.datasets.set_gate(urn, type, config={})    -> AttributeGateBinding
    kz.datasets.get_gate(urn)                     -> AttributeGateBinding
    kz.datasets.clear_gate(urn)                   -> None

Server-side correlates:
    POST   /api/catalog/datasets/
    GET    /api/catalog/datasets/by-urn?urn=...
    DELETE /api/catalog/datasets/by-urn?urn=...
    PUT    /api/catalog/datasets/{urn-encoded}/gate
    GET    /api/catalog/datasets/{urn-encoded}/gate
    DELETE /api/catalog/datasets/{urn-encoded}/gate

PR feedback H1: gate-binding endpoints URL-encode the URN segment.
PR feedback M2 (test gap): the previous URN ``urn:li:dataset:(local,demo,PROD)``
contained no ``/``, so ``quote(safe="")`` and ``quote(safe="/")`` produced
identical output — the regression guard was effectively a no-op. The new
``test_set_gate_encodes_file_platform_urn_with_slashes`` uses a real
file-platform URN (``urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/docs,PROD)``)
to actually exercise the encoding contract.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest


# ─── create / get / delete ────────────────────────────────────────────────


def test_create_posts_minimal_body(mock_client) -> None:
    """kz.datasets.create(name, platform) → POST /catalog/datasets/.

    PR feedback C1: server returns a bare URN string per OpenAPI
    (``201 type: string``), not a Dataset object. SDK returns the URN
    string for callers to subsequently ``kz.datasets.get(urn)``.
    """
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    expected_urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.expect("POST", "/catalog/datasets/", expected_urn)

    urn = DatasetsAPI(client=mock_client).create(name="demo", platform="file")

    assert isinstance(urn, str)
    assert urn == expected_urn

    body = mock_client.calls[0][2].get("json", {})
    assert body["name"] == "demo"
    assert body["platform"] == "file"


def test_create_notes_recent_dataset_change_for_consistency_retry(mock_client) -> None:
    """PR-feedback M1 (Codex correctness): after a successful POST the URN
    is registered with ``client._note_recent_dataset_change`` so a
    subsequent ``catalog.datasets.update_schema(urn, ...)`` gets the
    DataHub eventual-consistency 404-retry the legacy path provides.

    Without this, ``kz.datasets.create(...) → catalog.update_schema(...)``
    can 404 against an as-yet-unindexed dataset and surface as a hard
    failure to the caller.
    """
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    expected_urn = "urn:li:dataset:(local,registered,PROD)"
    mock_client.expect("POST", "/catalog/datasets/", expected_urn)

    # Add the bookkeeping hook to the mock — DatasetsAPI calls it iff present.
    seen: list[str] = []
    mock_client._note_recent_dataset_change = seen.append

    urn = DatasetsAPI(client=mock_client).create(name="registered", platform="file")

    assert urn == expected_urn
    assert seen == [expected_urn], (
        "DatasetsAPI.create must call client._note_recent_dataset_change(urn) "
        "so subsequent catalog.update_schema gets the DataHub consistency retry."
    )


def test_create_forwards_properties_and_environment(mock_client) -> None:
    """Optional kwargs (properties, environment) reach the server unchanged."""
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    mock_client.expect("POST", "/catalog/datasets/", "urn:li:dataset:(local,demo,DEV)")

    DatasetsAPI(client=mock_client).create(
        name="demo",
        platform="file",
        environment="DEV",
        properties={"path": "/data/demo"},
    )

    body = mock_client.calls[0][2].get("json", {})
    assert body["environment"] == "DEV"
    assert body["properties"] == {"path": "/data/demo"}


def test_get_passes_urn_as_query_param(mock_client) -> None:
    """kz.datasets.get(urn) → GET /catalog/datasets/by-urn?urn=..."""
    from kamiwaza_sdk.schemas.federation import DatasetRef
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.expect(
        "GET",
        "/catalog/datasets/by-urn",
        {
            "urn": urn,
            "name": "demo",
            "platform": "file",
            "environment": "PROD",
            "properties": {},
        },
    )

    ds = DatasetsAPI(client=mock_client).get(urn)
    assert isinstance(ds, DatasetRef)
    assert ds.urn == urn

    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("params") == {"urn": urn}


def test_delete_passes_urn_as_query_param(mock_client) -> None:
    """kz.datasets.delete(urn) → DELETE /catalog/datasets/by-urn?urn=..."""
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.expect("DELETE", "/catalog/datasets/by-urn", {"message": "deleted"})

    DatasetsAPI(client=mock_client).delete(urn)

    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("params") == {"urn": urn}


# ─── gate binding (M3-specific surface) ──────────────────────────────────


def test_set_gate_puts_to_dataset_scoped_endpoint(mock_client) -> None:
    """kz.datasets.set_gate puts to /catalog/datasets/{urn-encoded}/gate."""
    from kamiwaza_sdk.schemas.federation import AttributeGateBinding
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    encoded = quote(urn, safe="")
    mock_client.expect(
        "PUT",
        f"/catalog/datasets/{encoded}/gate",
        {
            "dataset_urn": urn,
            "type": "my_gate.ClassificationGate",
            "config": {"classification_field": "classification"},
            "gate_name": "classification-gate",
            "kind": "attribute",
        },
    )

    binding = DatasetsAPI(client=mock_client).set_gate(
        urn,
        type="my_gate.ClassificationGate",
        config={"classification_field": "classification"},
    )

    assert isinstance(binding, AttributeGateBinding)
    assert binding.kind == "attribute"
    assert binding.dataset_urn == urn

    body = mock_client.calls[0][2].get("json", {})
    assert body == {
        "type": "my_gate.ClassificationGate",
        "config": {"classification_field": "classification"},
    }


def test_set_gate_encodes_file_platform_urn_with_slashes(mock_client) -> None:
    """PR-feedback M2 regression guard: a real file-platform URN containing
    a filesystem path with ``/`` characters must be encoded so the path
    doesn't split into extra segments.

    Without ``quote(safe="")`` (i.e. with ``safe="/")``, the URN
    ``urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/docs,PROD)`` would
    route to ``/catalog/datasets/.../var/tmp/docs.../gate`` (four extra
    segments). With ``safe=""``, the slashes encode as ``%2F`` and the
    path stays single-segment.
    """
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/docs,PROD)"
    safe_empty = quote(urn, safe="")
    safe_slash = quote(urn, safe="/")

    # Sanity: encoding with `/` left safe would NOT escape the slashes,
    # so the two encodings differ. If they were identical (as for the
    # legacy ``(local,demo,PROD)`` URN), this test would not prove
    # anything about the safe="" contract.
    assert safe_empty != safe_slash, (
        "Test URN must actually exercise the `/` encoding — pick a URN "
        "with `/` characters or this regression guard is a no-op."
    )

    mock_client.expect(
        "PUT",
        f"/catalog/datasets/{safe_empty}/gate",
        {
            "dataset_urn": urn,
            "type": "x.Gate",
            "config": {},
            "gate_name": "x",
            "kind": "attribute",
        },
    )

    DatasetsAPI(client=mock_client).set_gate(urn, type="x.Gate")

    _method, path, _ = mock_client.calls[0]
    assert path == f"/catalog/datasets/{safe_empty}/gate"
    # Belt-and-suspenders: the wrongly-encoded path is NOT what was sent.
    assert path != f"/catalog/datasets/{safe_slash}/gate"


def test_set_gate_defaults_config_to_empty_dict(mock_client) -> None:
    """Omitting config sends an empty dict — server's config_schema()
    default-accepts gates with no configurable surface."""
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.expect(
        "PUT",
        f"/catalog/datasets/{quote(urn, safe='')}/gate",
        {
            "dataset_urn": urn,
            "type": "x.Gate",
            "config": {},
            "gate_name": "x",
            "kind": "attribute",
        },
    )

    DatasetsAPI(client=mock_client).set_gate(urn, type="x.Gate")

    body = mock_client.calls[0][2].get("json", {})
    assert body == {"type": "x.Gate", "config": {}}


def test_get_gate_returns_binding(mock_client) -> None:
    from kamiwaza_sdk.schemas.federation import AttributeGateBinding
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.expect(
        "GET",
        f"/catalog/datasets/{quote(urn, safe='')}/gate",
        {
            "dataset_urn": urn,
            "type": "g.G",
            "config": {},
            "gate_name": "g",
            "kind": "attribute",
        },
    )

    binding = DatasetsAPI(client=mock_client).get_gate(urn)

    assert isinstance(binding, AttributeGateBinding)
    assert binding.gate_name == "g"


def test_get_gate_raises_on_404_not_configured(mock_client) -> None:
    """404 not_configured surfaces as KamiwazaError per T5.10 mapping."""
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.raise_on(
        "GET",
        f"/catalog/datasets/{quote(urn, safe='')}/gate",
        KamiwazaError("not_configured", status_code=404),
    )

    with pytest.raises(KamiwazaError):
        DatasetsAPI(client=mock_client).get_gate(urn)


def test_clear_gate_sends_delete(mock_client) -> None:
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.expect(
        "DELETE",
        f"/catalog/datasets/{quote(urn, safe='')}/gate",
        {"deleted": True, "previous_type": "g.G"},
    )

    DatasetsAPI(client=mock_client).clear_gate(urn)


# ─── T5.17-full — 4xx error-mapping coverage ──────────────────────────────


def test_set_gate_400_wrong_kind_surfaces_as_kamiwaza_error(mock_client) -> None:
    """Server rejects binding an ExecutionGate as a dataset gate (T2.5
    wrong_kind) — surfaces as KamiwazaError with status_code=400."""
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.raise_on(
        "PUT",
        f"/catalog/datasets/{quote(urn, safe='')}/gate",
        KamiwazaError("wrong_kind", status_code=400),
    )

    with pytest.raises(KamiwazaError) as exc_info:
        DatasetsAPI(client=mock_client).set_gate(urn, type="x.ExecGate")
    assert exc_info.value.status_code == 400


def test_set_gate_400_schema_validation_failed_surfaces_as_kamiwaza_error(
    mock_client,
) -> None:
    """T2.6 jsonschema validation failure → 400 schema_validation_failed."""
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,demo,PROD)"
    mock_client.raise_on(
        "PUT",
        f"/catalog/datasets/{quote(urn, safe='')}/gate",
        KamiwazaError("schema_validation_failed", status_code=400),
    )

    with pytest.raises(KamiwazaError) as exc_info:
        DatasetsAPI(client=mock_client).set_gate(urn, type="x.Gate", config={})
    assert exc_info.value.status_code == 400


def test_set_gate_404_dataset_not_found_surfaces_as_kamiwaza_error(
    mock_client,
) -> None:
    """PUT against an unknown dataset URN → 404 dataset_not_found."""
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    urn = "urn:li:dataset:(local,nonexistent,PROD)"
    mock_client.raise_on(
        "PUT",
        f"/catalog/datasets/{quote(urn, safe='')}/gate",
        KamiwazaError("dataset_not_found", status_code=404),
    )

    with pytest.raises(KamiwazaError) as exc_info:
        DatasetsAPI(client=mock_client).set_gate(urn, type="x.Gate")
    assert exc_info.value.status_code == 404


def test_get_dataset_404_surfaces_as_kamiwaza_error(mock_client) -> None:
    """GET dataset by unknown URN returns 404."""
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.datasets import DatasetsAPI

    mock_client.raise_on(
        "GET",
        "/catalog/datasets/by-urn",
        KamiwazaError("Dataset not found", status_code=404),
    )

    with pytest.raises(KamiwazaError):
        DatasetsAPI(client=mock_client).get("urn:li:dataset:(local,nope,PROD)")
