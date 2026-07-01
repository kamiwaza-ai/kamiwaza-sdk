"""Unit tests for the connector dispatch param-tolerance helper."""

import pytest

from kamiwaza_sdk.connectors.server_kit import accepted_params

pytestmark = pytest.mark.unit


async def _content_with_drive(*, node_id, drive_id, subject_token=None):
    return {"node_id": node_id, "drive_id": drive_id}


async def _content_plain(*, node_id, subject_token=None):
    return {"node_id": node_id}


async def _accepts_kwargs(*, node_id, subject_token=None, **rest):
    return {"node_id": node_id, "rest": rest}


def test_drops_undeclared_param():
    """A hint the op does not declare (mime_type) is dropped, not passed through."""
    got = accepted_params(
        _content_plain, {"node_id": "n1", "mime_type": "text/markdown"}
    )
    assert got == {"node_id": "n1"}


def test_keeps_declared_param():
    """A param the op declares (drive_id) is kept; an extra hint is still dropped."""
    got = accepted_params(
        _content_with_drive,
        {"node_id": "n1", "drive_id": "d1", "mime_type": "application/pdf"},
    )
    assert got == {"node_id": "n1", "drive_id": "d1"}


def test_var_keyword_op_keeps_everything():
    """An op that declares **kwargs opts out of filtering entirely."""
    params = {"node_id": "n1", "mime_type": "x", "anything": "y"}
    assert accepted_params(_accepts_kwargs, params) == params


def test_missing_required_param_still_raises_at_call():
    """Filtering never invents a required param, so a real omission stays loud."""
    filtered = accepted_params(_content_with_drive, {"node_id": "n1"})
    assert filtered == {"node_id": "n1"}  # drive_id absent -> op will TypeError
    with pytest.raises(TypeError):
        _content_with_drive(**filtered)
