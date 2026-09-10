"""ENG-10427 — typed delegated-access job submission contract."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.schemas.delegated_jobs import (
    DatasetDelegatedAccess,
    DelegatedAccess,
    ModelDelegatedAccess,
)
from kamiwaza_sdk.services.jobs_federation import JobsAPI


_DATASET_URN = "urn:li:dataset:(urn:li:dataPlatform:file,claims,PROD)"
_DEPLOYMENT_ID = UUID("7adcb7f4-9de0-4ee4-8cb6-73db11b3ae89")


def test_submit_async_serializes_typed_delegated_access(mock_client) -> None:
    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": "job-1"})
    access = DelegatedAccess(
        datasets=(
            DatasetDelegatedAccess(
                urn=_DATASET_URN,
                operations=("retrieve", "discover", "read"),
            ),
        ),
        models=(
            ModelDelegatedAccess(
                deployment_id=_DEPLOYMENT_ID,
                operations=("chat", "discover"),
            ),
        ),
    )

    job_id = JobsAPI(client=mock_client).submit_async(
        entrypoint="python summarize.py",
        delegated_access=access,
        python_packages=["Humanize==4.13.0"],
    )

    assert job_id == "job-1"
    assert mock_client.calls[0][2]["json"]["delegated_access"] == {
        "datasets": [
            {
                "urn": _DATASET_URN,
                "operations": ["discover", "read", "retrieve"],
            }
        ],
        "models": [
            {
                "deployment_id": str(_DEPLOYMENT_ID),
                "operations": ["discover", "chat"],
            }
        ],
    }
    assert mock_client.calls[0][2]["json"]["python_packages"] == ["humanize==4.13.0"]


def test_run_accepts_mapping_and_validates_before_network(mock_client) -> None:
    with pytest.raises(ValidationError, match="dataset urn must be exact"):
        JobsAPI(client=mock_client).run(
            entrypoint="python summarize.py",
            delegated_access={
                "datasets": [{"urn": "urn:li:dataset:*", "operations": ["discover"]}]
            },
        )

    assert mock_client.calls == []


def test_existing_submission_body_is_unchanged_when_access_absent(mock_client) -> None:
    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": "ordinary"})

    JobsAPI(client=mock_client).submit_async(entrypoint="python ordinary.py")

    assert mock_client.calls[0][2]["json"] == {"entrypoint": "python ordinary.py"}


@pytest.mark.parametrize(
    "python_packages",
    [
        ["humanize"],
        ["humanize>=4.13.0"],
        ["humanize @ https://packages.example/humanize.whl"],
        ["humanize==4.13.0", "Humanize==4.13.0"],
        [f"package-{index}==1.0.0" for index in range(33)],
    ],
)
def test_python_packages_fail_before_network_when_not_exact_or_bounded(
    mock_client, python_packages: list[str]
) -> None:
    with pytest.raises((ValidationError, ValueError)):
        JobsAPI(client=mock_client).submit_async(
            entrypoint="python summarize.py",
            delegated_access={
                "datasets": [{"urn": _DATASET_URN, "operations": ["discover"]}]
            },
            python_packages=python_packages,
        )

    assert mock_client.calls == []


def test_python_packages_require_delegated_access(mock_client) -> None:
    with pytest.raises(ValueError, match="require delegated_access"):
        JobsAPI(client=mock_client).submit_async(
            entrypoint="python ordinary.py",
            python_packages=["humanize==4.13.0"],
        )

    assert mock_client.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"datasets": []},
        {"datasets": [{"urn": _DATASET_URN, "operations": ["discover", "discover"]}]},
        {
            "models": [
                {
                    "deployment_id": str(_DEPLOYMENT_ID),
                    "operations": ["invoke"],
                }
            ]
        },
    ],
)
def test_delegated_access_rejects_empty_duplicate_or_unknown_operations(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DelegatedAccess.model_validate(payload)
