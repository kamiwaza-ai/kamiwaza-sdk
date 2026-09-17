from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.schemas.enclaves import ConnectorCreate
from kamiwaza_sdk.schemas.markings import Marking
from kamiwaza_sdk.schemas.skills import SkillLibraryUpdateRequest
from kamiwaza_sdk.schemas.workrooms import CreateWorkroom, UpdateWorkroom
from kamiwaza_sdk.services.markings import MarkingsService

pytestmark = pytest.mark.unit


def sample_marking():
    return Marking(
        profile_id="commercial",
        profile_revision="1",
        level_id="private",
        raw_text="Company Private",
        attributes={"project": ["alpha"]},
    )


def test_disabled_config_does_not_invent_vocabulary():
    client = Mock()
    client.get.return_value = {"enabled": False}
    config = MarkingsService(client).config()
    assert not config.enabled
    assert config.levels == []
    assert config.profile_id is None
    client.get.assert_called_once_with("/security/markings/config")


@pytest.fixture
def config_response():
    return {
        "enabled": True,
        "profile_id": "commercial",
        "profile_revision": "1",
        "levels": [
            {
                "id": "private",
                "name": "Company Private",
                "rank": 1,
                "background_color": "#112233",
                "foreground_color": "#ffffff",
                "future_level_field": "preserved",
            }
        ],
        "future_config_field": {"supported": True},
    }


def test_config_rejects_level_without_assignable(config_response):
    client = Mock()
    client.get.return_value = config_response
    with pytest.raises(ValidationError) as exc:
        MarkingsService(client).config()
    assert [(error["loc"], error["type"]) for error in exc.value.errors()] == [
        (("levels", 0, "assignable"), "missing")
    ]


@pytest.mark.parametrize("assignable", [True, False])
def test_config_preserves_explicit_assignable_and_response_extras(
    config_response, assignable
):
    config_response["levels"][0]["assignable"] = assignable
    client = Mock()
    client.get.return_value = config_response
    config = MarkingsService(client).config()
    assert config.levels[0].assignable is assignable
    assert config.levels[0].model_dump() == config_response["levels"][0]
    assert config.model_dump()["future_config_field"] == {"supported": True}
    client.get.assert_called_once_with("/security/markings/config")


def test_parse_sends_raw_input_and_preserves_provider_attributes():
    client = Mock()
    marking = sample_marking()
    display = {
        "text": "Company Private",
        "background_color": "#112233",
        "foreground_color": "#ffffff",
    }
    client.post.return_value = {"marking": marking.model_dump(), "display": display}
    parsed = MarkingsService(client).parse("Company Private", default_for="document")
    assert parsed.marking == marking
    assert parsed.display.text == "Company Private"
    client.post.assert_called_once_with(
        "/security/markings/parse",
        json={"text": "Company Private", "default_for": "document"},
    )


def test_compose_uses_normalized_envelope_and_server_display():
    client = Mock()
    client.post.return_value = {"top": None, "bottom": None}
    marking = sample_marking()
    result = MarkingsService(client).compose([marking], surface="workroom")
    assert result.top is None
    client.post.assert_called_once_with(
        "/security/markings/compose",
        json={"markings": [marking.model_dump()], "surface": "workroom"},
    )


def test_marking_schema_roundtrips_nested_attributes_and_future_fields():
    value = sample_marking().model_dump()
    value["attributes"]["nested"] = {"approved": True}
    value["future"] = "preserved"
    assert Marking.model_validate(value).model_dump() == value
    with pytest.raises(ValidationError):
        Marking(profile_id="", profile_revision="1", level_id="private")


def test_resource_requests_do_not_apply_default_marking():
    assert CreateWorkroom(name="Example", type="persistent").marking is None
    assert (
        ConnectorCreate(
            name="Source",
            source_type="file",
            connector_type="file",
            connection_config={},
        ).level_boundary
        is None
    )
    assert UpdateWorkroom().model_dump(exclude_unset=True) == {}
    assert UpdateWorkroom(marking=None).model_dump(exclude_unset=True) == {
        "marking": None
    }
    assert (
        SkillLibraryUpdateRequest(marking=sample_marking()).marking == sample_marking()
    )


def test_workroom_requests_reject_unknown_fields_instead_of_dropping_them():
    with pytest.raises(ValidationError):
        CreateWorkroom(name="Example", type="persistent", unsupported_marking="private")
    with pytest.raises(ValidationError):
        UpdateWorkroom(unsupported_marking="private")
