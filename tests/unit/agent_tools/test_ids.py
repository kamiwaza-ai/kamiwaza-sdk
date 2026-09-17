from __future__ import annotations

import warnings

import pytest

from kamiwaza_sdk.agent_tools.ids import (
    UNPUBLISHED,
    UnpublishedOperationError,
    published_id,
    selector,
    unpublished_reason,
)

pytestmark = pytest.mark.unit


def _client():
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return KamiwazaClient(base_url="http://localhost:7777/api")


@pytest.mark.parametrize(
    ("op_selector", "expected"),
    [
        # The method name already carries the namespace noun, so it is not repeated.
        ("models.list_models", "list_models"),
        ("apps.list_apps", "list_apps"),
        # The namespace noun is absent from the method name, so it is appended.
        ("agents.create", "create_agents"),
        ("cluster.get_node_by_id", "get_node_by_id_cluster"),
        ("serving.deploy_model", "deploy_model_serving"),
        # Singular method noun against a plural namespace still de-duplicates.
        ("agents.bind_skill", "bind_skill_agents"),
        # Nested namespaces flatten in order.
        ("postgres.roles.reset_password", "reset_password_postgres_roles"),
        # camelCase input yields the same snake_case output as its snake twin.
        ("serving.deployModel", "deploy_model_serving"),
    ],
)
def test_published_id_places_the_verb_first(op_selector: str, expected: str) -> None:
    assert published_id(op_selector) == expected


def test_published_id_rejects_a_selector_with_no_namespace() -> None:
    with pytest.raises(ValueError, match="dotted service.method"):
        published_id("list_models")


def test_published_id_rejects_an_empty_segment() -> None:
    with pytest.raises(ValueError, match="dotted service.method"):
        published_id("models.")


def test_every_client_operation_has_a_unique_published_id() -> None:
    """FR-005d, asserted per entry rather than on a sample.

    A collision would silently shadow one operation with another, and the agent
    would call the wrong thing while the surface still looked complete.
    """
    from kamiwaza_sdk.agent_tools.spec_index import build_index

    index = build_index(_client())
    seen: dict[str, str] = {}
    collisions: list[tuple[str, str, str]] = []
    for entry in index:
        assert entry.published_id == published_id(entry.selector)
        assert entry.published_id.islower()
        assert " " not in entry.published_id
        if entry.published_id in seen:
            collisions.append(
                (entry.published_id, seen[entry.published_id], entry.selector)
            )
        seen[entry.published_id] = entry.selector
    assert collisions == []


def test_no_published_identifier_is_a_dotted_selector() -> None:
    """The published form is never the internal form, per FR-005d."""
    from kamiwaza_sdk.agent_tools.spec_index import build_index

    for entry in build_index(_client()):
        assert "." not in entry.published_id


def test_selector_joins_service_and_method() -> None:
    assert selector("models", "list_models") == "models.list_models"


def test_unpublished_entries_state_a_reason() -> None:
    assert UNPUBLISHED, "the unpublished set must not be empty"
    for op_selector, reason in UNPUBLISHED.items():
        assert reason.reason.strip(), f"{op_selector} has no stated reason"
        assert reason.message().endswith("."), f"{op_selector} reason is not prose"


def test_unpublished_reason_returns_none_for_a_published_operation() -> None:
    assert unpublished_reason("models.list_models") is None


def test_local_helpers_are_withheld_by_name() -> None:
    """A method that makes no platform call is not an operation to publish.

    Named rather than counted: each of these reads its own argument, encodes a
    string, or builds a local object, so an agent asking for platform
    operations can do nothing with it. ``auth.require_admin`` is the one that
    shows the cost, because it wins a keyword search for "admin".
    """
    for op_selector in (
        "auth.require_admin",
        "catalog.encode_urn",
        "catalog.datasets.encode_path_urn",
        "catalog.containers.encode_path_urn",
        "catalog.secrets.encode_path_urn",
        "models.auto_selector",
        "embedding.get_embedder",
        "subjects.grants",
    ):
        reason = unpublished_reason(op_selector)
        assert reason is not None, f"{op_selector} makes no platform call"


def test_an_operation_whose_body_only_raises_is_withheld() -> None:
    """``embedding.call`` raises DeprecationWarning for every argument.

    Named rather than counted, because publishing it gives an agent an
    exception in place of an embedding.
    """
    reason = unpublished_reason("embedding.call")
    assert reason is not None, "embedding.call only raises"
    assert "exception" in reason.message()


def test_no_withheld_reason_directs_an_agent_to_a_withheld_operation() -> None:
    """A replacement pointer is useless if the replacement is withheld too."""
    for op_selector, reason in UNPUBLISHED.items():
        target = reason.superseded_by
        if target is None:
            continue
        assert target not in UNPUBLISHED, (
            f"{op_selector} directs an agent to {target}, which is withheld"
        )


def test_deprecated_tool_service_names_its_replacement() -> None:
    reason = unpublished_reason("tools.list_deployments")
    assert reason is not None
    assert reason.superseded_by == "extensions"
    assert "extensions" in reason.message()


def test_error_carries_the_reason_not_just_the_name() -> None:
    """FR-005e: asking for a withheld operation must not look like "unknown"."""
    reason = unpublished_reason("auth.create_pat")
    assert reason is not None
    error = UnpublishedOperationError("create_pat_auth", reason.message())
    assert error.reason == reason.message()
    assert "credential" in str(error)
