"""T7.3 / ENG-5037 — Federation-aware Pydantic models on the canonical
``kamiwaza_sdk.schemas`` surface.

Verifies the M1+ federation-aware Pydantic models (previously at
``kamiwaza/models.py``) have migrated to ``kamiwaza_sdk/schemas/federation.py``
per design v0.3.7 §4.2.11.

Includes the JobResult field-gap fix: the legacy ``kamiwaza/models.py``
JobResult declares only ``job_id``, ``status``, ``result``, ``error``,
``audit_actor`` — the server actually returns 4 additional fields
(``ray_job_id``, ``error_type``, ``error_message``, ``duration_seconds``)
which are currently paper-thinned via ``extra="allow"``. T7.3 declares
them properly so type-checked customer code can access them without
``getattr(model, "ray_job_id", None)``.

All models preserve ``extra="allow"`` for forward compatibility per
``.ai/knowledge/failures/common-pitfalls.md``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Models importable from kamiwaza_sdk.schemas.federation
# ---------------------------------------------------------------------------


def test_all_federation_models_importable_from_canonical_surface() -> None:
    """Every model in kamiwaza/models.py is importable from the canonical
    kamiwaza_sdk.schemas.federation module. The set is fixed — additions
    to this set are deliberate design changes."""
    from kamiwaza_sdk.schemas.federation import (
        AttributeGateBinding,
        AttributeSchema,
        AttributeSchemaList,
        BrokeredUser,
        ClusterDiagnostics,
        ClusterOperations,
        DatasetRef,
        DiagnoseIssue,
        ExecutionGateBinding,
        Federation,
        FixOutcome,
        FixResult,
        GateDiscovery,
        Grant,
        JobResult,
        Subject,
    )

    # Sanity check: all 16 are non-None class objects (the import itself
    # is the assertion; this just keeps Pyright/lint from flagging unused).
    classes = (
        AttributeGateBinding,
        AttributeSchema,
        AttributeSchemaList,
        BrokeredUser,
        ClusterDiagnostics,
        ClusterOperations,
        DatasetRef,
        DiagnoseIssue,
        ExecutionGateBinding,
        Federation,
        FixOutcome,
        FixResult,
        GateDiscovery,
        Grant,
        JobResult,
        Subject,
    )
    for cls in classes:
        assert isinstance(cls, type), f"{cls!r} must be a class"


# ---------------------------------------------------------------------------
# Forward-compat — extra='allow' preserved across all models
# ---------------------------------------------------------------------------


def test_all_models_preserve_extra_allow() -> None:
    """Per common-pitfalls.md, every SDK schema must opt into extra='allow'
    so pinned-wheel customers don't break when the server adds fields."""
    from kamiwaza_sdk.schemas.federation import (
        AttributeGateBinding,
        AttributeSchema,
        BrokeredUser,
        ExecutionGateBinding,
        Federation,
        JobResult,
        Subject,
    )

    for cls in [
        Federation,
        BrokeredUser,
        Subject,
        JobResult,
        AttributeGateBinding,
        ExecutionGateBinding,
        AttributeSchema,
    ]:
        assert cls.model_config.get("extra") == "allow", (
            f"{cls.__name__} must set extra='allow' for forward-compat"
        )


# ---------------------------------------------------------------------------
# JobResult — declare the 4 fields previously papered over by extra='allow'
# ---------------------------------------------------------------------------


def test_job_result_declares_ray_job_id() -> None:
    """T7.3 declares ray_job_id as a typed Optional[str] field. Customer
    code that branches on ``result.ray_job_id is None`` gets type-checker
    support."""
    from kamiwaza_sdk.schemas.federation import JobResult

    result = JobResult(
        job_id="job-123",
        status="SUCCEEDED",
        ray_job_id="ray-abc-xyz",
    )
    assert result.ray_job_id == "ray-abc-xyz"
    # Default None when not provided.
    result_no_ray = JobResult(job_id="j2", status="SUCCEEDED")
    assert result_no_ray.ray_job_id is None


def test_job_result_declares_error_type() -> None:
    """T7.3 declares error_type as a typed Optional[str] field."""
    from kamiwaza_sdk.schemas.federation import JobResult

    result = JobResult(
        job_id="job-456",
        status="FAILED",
        error="generic error message",
        error_type="OBOExchangeFailedError",
    )
    assert result.error_type == "OBOExchangeFailedError"


def test_job_result_declares_error_message() -> None:
    """T7.3 declares error_message as a typed Optional[str] field — distinct
    from the existing ``error`` field which carries the short summary; the
    long-form structured message lives in error_message."""
    from kamiwaza_sdk.schemas.federation import JobResult

    result = JobResult(
        job_id="job-789",
        status="FAILED",
        error="Quick summary",
        error_message="Long-form error with stack trace + context...",
    )
    assert result.error_message == "Long-form error with stack trace + context..."


def test_job_result_declares_duration_seconds() -> None:
    """T7.3 declares duration_seconds as a typed Optional[float] — server
    populates this on terminal job states (SUCCEEDED, FAILED, CANCELED)."""
    from kamiwaza_sdk.schemas.federation import JobResult

    result = JobResult(
        job_id="job-abc",
        status="SUCCEEDED",
        duration_seconds=42.5,
    )
    assert result.duration_seconds == 42.5


def test_job_result_existing_fields_unchanged() -> None:
    """Backward-compat — the 5 original fields (job_id, status, result,
    error, audit_actor) keep their shape so existing callers don't break."""
    from kamiwaza_sdk.schemas.federation import JobResult

    result = JobResult(
        job_id="job-1",
        status="SUCCEEDED",
        result={"output": "hello"},
        error=None,
        audit_actor="cdr-baker@lyra-cluster-uuid",
    )
    assert result.job_id == "job-1"
    assert result.status == "SUCCEEDED"
    assert result.result == {"output": "hello"}
    assert result.error is None
    assert result.audit_actor == "cdr-baker@lyra-cluster-uuid"


# ---------------------------------------------------------------------------
# Federation + BrokeredUser — round-trip shape (no behavior change from M1)
# ---------------------------------------------------------------------------


def test_federation_model_round_trip() -> None:
    """Federation parses common server response shapes."""
    from kamiwaza_sdk.schemas.federation import Federation

    fed = Federation(
        id="fed-orion-123",
        status="PAIRED",
        remote_cluster_id="orion-uuid",
        remote_cluster_name="ORION",
        remote_ips=[{"ip": "10.0.0.99", "primary": True}],
        callback_hostname="edge.lyra.example.com",
    )
    assert fed.id == "fed-orion-123"
    assert fed.status == "PAIRED"
    assert fed.remote_cluster_name == "ORION"


def test_brokered_user_model_round_trip() -> None:
    """BrokeredUser parses cluster_federation_users rows."""
    from kamiwaza_sdk.schemas.federation import BrokeredUser

    user = BrokeredUser(
        federation_id="fed-1",
        external_id="cdr-baker@lyra-cluster-uuid",
        auto_provisioned=True,
        created_at=datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc),
        initial_tuples=[
            {
                "subject": "user:cdr-baker",
                "relation": "viewer",
                "object": "cluster:ORION",
            }
        ],
    )
    assert user.auto_provisioned is True
    assert user.initial_tuples is not None
    assert len(user.initial_tuples) == 1


# ---------------------------------------------------------------------------
# AttributeSchema — M3.1 lifecycle shape
# ---------------------------------------------------------------------------


def test_attribute_schema_full_shape() -> None:
    """AttributeSchema carries the full M3.1 vocabulary lifecycle fields."""
    from kamiwaza_sdk.schemas.federation import AttributeSchema

    schema = AttributeSchema(
        name="clearance",
        type="string",
        state="declared",
        authority="local_admin",
        sensitive=False,
        schema_version="1.0",
        declared_at=datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert schema.name == "clearance"
    assert schema.type == "string"
    assert schema.state == "declared"
    assert schema.authority == "local_admin"
    assert schema.sensitive is False


# ---------------------------------------------------------------------------
# Legacy kamiwaza.* namespace was removed in WS-M3.2 (design v0.3.7 §4.2.11
# revised OQ-17). Tests that asserted the bridge identity are deleted.
# ---------------------------------------------------------------------------


def test_kamiwaza_namespace_is_removed() -> None:
    """The interim ``kamiwaza`` top-level package (from the reversed v0.2.0
    decision) is gone in v0.3.7. New code uses ``kamiwaza_sdk`` exclusively.
    """
    import pytest

    with pytest.raises(ModuleNotFoundError):
        __import__("kamiwaza.models")
