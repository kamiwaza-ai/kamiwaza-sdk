"""T5.11 / ENG-4682 — Skeleton Pydantic models for SDK return types.

Verifies the WS-M1-scoped models (Federation, JobResult, BrokeredUser)
parse server response shapes correctly and tolerate forward-compatible
field additions per the SDK's common-pitfalls guidance.

Subsequent tickets layer additional models (Subject, Dataset,
ClusterCapabilities, Operation, …) — those are explicitly scoped out of
T5.11.
"""

from __future__ import annotations

from typing import Any


def test_federation_parses_minimal_payload() -> None:
    """Server sometimes returns minimal federation rows (e.g. on creation
    before pairing completes). Required fields are id + status; everything
    else is optional."""
    from kamiwaza.models import Federation

    fed = Federation.model_validate({"id": "0a0358eb-...", "status": "WAITING"})
    assert fed.id == "0a0358eb-..."
    assert fed.status == "WAITING"
    assert fed.remote_cluster_name is None


def test_federation_parses_full_payload() -> None:
    """Full PAIRED federation payload exercises every documented field."""
    from kamiwaza.models import Federation

    fed = Federation.model_validate(
        {
            "id": "fed-123",
            "status": "PAIRED",
            "remote_cluster_id": "ORION-cluster-uuid",
            "remote_cluster_name": "ORION",
            "remote_ips": [{"ip": "10.0.0.99", "primary": True}],
            "callback_hostname": "edge.lyra.example.com",
        }
    )
    assert fed.remote_cluster_name == "ORION"
    assert fed.remote_ips == [{"ip": "10.0.0.99", "primary": True}]
    assert fed.callback_hostname == "edge.lyra.example.com"


def test_federation_allows_extra_fields() -> None:
    """Forward compat: when the server adds fields the SDK doesn't yet
    know about, parsing must NOT fail (per .ai/knowledge/failures/
    common-pitfalls.md). Extras land on the model but are accessible."""
    from kamiwaza.models import Federation

    fed = Federation.model_validate(
        {
            "id": "fed-123",
            "status": "PAIRED",
            "future_field_added_in_v1_1": "some value",
        }
    )
    # Field is preserved (Pydantic v2 with extra='allow' stores extras
    # in __pydantic_extra__).
    assert getattr(fed, "future_field_added_in_v1_1") == "some value"


def test_brokered_user_parses_minimal_payload() -> None:
    """BrokeredUser is the FederationUser dataclass shape from design
    §4.2.9c, but as a Pydantic model so the SDK can return validated
    objects from FederationUsersAPI calls."""
    from kamiwaza.models import BrokeredUser

    user = BrokeredUser.model_validate(
        {
            "federation_id": "fed-123",
            "external_id": "cdr-baker@lyra-cluster-uuid",
        }
    )
    assert user.federation_id == "fed-123"
    assert user.external_id == "cdr-baker@lyra-cluster-uuid"
    assert user.auto_provisioned is False  # Default before first ingress


def test_brokered_user_parses_provisioned_payload() -> None:
    """After first mesh request, the user is auto-provisioned and the
    server flips the flag + sets created_at + records initial_tuples."""
    from kamiwaza.models import BrokeredUser

    user = BrokeredUser.model_validate(
        {
            "federation_id": "fed-123",
            "external_id": "cdr-baker@lyra-cluster-uuid",
            "auto_provisioned": True,
            "created_at": "2026-05-09T12:34:56Z",
            "initial_tuples": [
                {
                    "subject": "user:cdr-baker@lyra-cluster-uuid",
                    "relation": "viewer",
                    "object": "cluster:ORION",
                }
            ],
        }
    )
    assert user.auto_provisioned is True
    assert user.created_at is not None
    assert user.initial_tuples is not None
    assert len(user.initial_tuples) == 1


def test_job_result_parses_succeeded_payload() -> None:
    """Most common JobResult shape — synchronous /run response with
    status=SUCCEEDED and a result body."""
    from kamiwaza.models import JobResult

    result = JobResult.model_validate(
        {
            "job_id": "job-abc",
            "status": "SUCCEEDED",
            "result": {"answer": "42", "tokens": 1024},
            "audit_actor": "cdr-baker@LYRA",
        }
    )
    assert result.job_id == "job-abc"
    assert result.status == "SUCCEEDED"
    assert result.result == {"answer": "42", "tokens": 1024}
    assert result.audit_actor == "cdr-baker@LYRA"


def test_job_result_parses_failed_payload() -> None:
    """FAILED jobs include error context but no result body; the model
    must accept the absence of result without raising."""
    from kamiwaza.models import JobResult

    result = JobResult.model_validate(
        {
            "job_id": "job-abc",
            "status": "FAILED",
            "error": "Ray task raised TypeError: bad arg",
        }
    )
    assert result.status == "FAILED"
    assert result.result is None


def test_models_export_at_top_level() -> None:
    """Convenience top-level imports — kamiwaza.Federation, etc. — for
    customer code that wants to construct/inspect models without
    drilling into kamiwaza.models."""
    from kamiwaza import BrokeredUser as TopBrokeredUser
    from kamiwaza import Federation as TopFederation
    from kamiwaza import JobResult as TopJobResult
    from kamiwaza.models import BrokeredUser, Federation, JobResult

    assert TopFederation is Federation
    assert TopBrokeredUser is BrokeredUser
    assert TopJobResult is JobResult


def test_models_in_all_export_list() -> None:
    """Models registered in kamiwaza.__all__ so star-imports surface
    them and IDE autocomplete works without manual import."""
    import kamiwaza

    for name in ("Federation", "JobResult", "BrokeredUser"):
        assert name in kamiwaza.__all__, f"{name} missing from __all__"


def _ensure_dict_round_trip(payload: dict[str, Any], cls: Any) -> None:
    """Helper: parse → dump → re-parse should be a fixed point for
    fields the model declares."""
    instance = cls.model_validate(payload)
    dumped = instance.model_dump(exclude_none=True)
    re_parsed = cls.model_validate(dumped)
    assert re_parsed == instance


def test_federation_round_trip() -> None:
    from kamiwaza.models import Federation

    _ensure_dict_round_trip(
        {
            "id": "fed-123",
            "status": "PAIRED",
            "remote_cluster_name": "ORION",
        },
        Federation,
    )
