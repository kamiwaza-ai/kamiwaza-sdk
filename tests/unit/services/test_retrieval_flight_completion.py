"""High-level Flight clean-EOF completion verification tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# High-level clean-EOF completion verification
# ---------------------------------------------------------------------------


def _retrieval_job():
    from kamiwaza_sdk.schemas.retrieval import (
        DatasetDescriptor,
        FlightEndpoint,
        GrpcHandshake,
        RetrievalJob,
        TransportType,
    )

    return RetrievalJob(
        job_id="job-completion",
        transport=TransportType.GRPC,
        status="STREAMING",
        dataset=DatasetDescriptor(urn="urn:test", platform="test"),
        grpc=GrpcHandshake(
            endpoints=[FlightEndpoint(location="grpc+tls://host:6130")],
            token="tok",
            expires_at="2099-01-01T00:00:00Z",
            protocol="arrow-flight",
        ),
    )


@pytest.mark.parametrize("status", ["COMPLETED", "completed", "complete"])
def test_flight_batches_verifies_completed_after_clean_exhaustion(status):
    from kamiwaza_sdk.services.retrieval import RetrievalService

    service = RetrievalService(MagicMock(session=None))
    service.get_job = MagicMock(return_value=MagicMock(status=status))

    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        return_value=iter(["batch"]),
    ):
        assert list(service.flight_batches(_retrieval_job())) == ["batch"]

    service.get_job.assert_called_once_with("job-completion")


def test_flight_batches_rejects_clean_eof_before_completed():
    from kamiwaza_sdk.exceptions import FlightIncompleteStreamError
    from kamiwaza_sdk.services.retrieval import RetrievalService

    service = RetrievalService(MagicMock(session=None))
    service.get_job = MagicMock(return_value=MagicMock(status="STREAMING"))

    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
            return_value=iter(()),
        ),
        pytest.raises(FlightIncompleteStreamError) as exc_info,
    ):
        list(service.flight_batches(_retrieval_job()))

    assert exc_info.value.job_id == "job-completion"
    assert exc_info.value.status == "STREAMING"


def test_flight_batches_wraps_completion_lookup_failure():
    from kamiwaza_sdk.exceptions import FlightIncompleteStreamError, KamiwazaError
    from kamiwaza_sdk.services.retrieval import RetrievalService

    original_error = KamiwazaError("status unavailable")
    service = RetrievalService(MagicMock(session=None))
    service.get_job = MagicMock(side_effect=original_error)

    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
            return_value=iter(()),
        ),
        pytest.raises(FlightIncompleteStreamError) as exc_info,
    ):
        list(service.flight_batches(_retrieval_job()))

    assert exc_info.value.status is None
    assert exc_info.value.__cause__ is original_error


def test_flight_batches_early_close_skips_completion_check():
    from kamiwaza_sdk.services.retrieval import RetrievalService

    closed: list[bool] = []

    def source():
        try:
            yield "batch"
            yield "unused"
        finally:
            closed.append(True)

    service = RetrievalService(MagicMock(session=None))
    service.get_job = MagicMock()
    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        return_value=source(),
    ):
        batches = service.flight_batches(_retrieval_job())
        assert next(batches) == "batch"
        batches.close()

    assert closed == [True]
    service.get_job.assert_not_called()
