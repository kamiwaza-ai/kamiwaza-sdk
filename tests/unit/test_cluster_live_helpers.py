import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration.test_cluster_live import _is_cluster_probe_not_authorized

pytestmark = pytest.mark.unit


def test_cluster_probe_not_authorized_detects_stable_rebac_denial() -> None:
    error = APIError(
        "not authorized",
        status_code=403,
        response_data={"detail": "not_authorized_to_probe_cluster"},
    )

    assert _is_cluster_probe_not_authorized(error) is True


@pytest.mark.parametrize(
    ("status_code", "response_data"),
    [
        (401, {"detail": "not_authorized_to_probe_cluster"}),
        (403, {"detail": "some_other_denial"}),
        (403, {"detail": {"reason": "not_authorized_to_probe_cluster"}}),
        (403, None),
        (403, "not_authorized_to_probe_cluster"),
    ],
)
def test_cluster_probe_not_authorized_rejects_other_errors(
    status_code: int,
    response_data: object,
) -> None:
    error = APIError(
        "not a cluster probe ReBAC denial",
        status_code=status_code,
        response_data=response_data,
    )

    assert _is_cluster_probe_not_authorized(error) is False
