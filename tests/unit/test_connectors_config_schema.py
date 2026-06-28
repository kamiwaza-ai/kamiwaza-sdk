"""Tests for the declarative connector config contract."""

from __future__ import annotations

import json

import pytest
from kamiwaza_sdk.connectors.config_schema import (
    ConfigField,
    ConfigOption,
    ConfigOutput,
    ConfigSchema,
    ConfigType,
    FieldWidth,
    Importance,
)
from kamiwaza_sdk.connectors.exceptions import InvalidConfigException

pytestmark = pytest.mark.unit


def _schema() -> ConfigSchema:
    return ConfigSchema(
        (
            ConfigField("client_id", description="OAuth client id"),
            ConfigField("client_secret", secret=True),
            ConfigField("timeout", type=ConfigType.INTEGER, required=False, default=30),
        )
    )


class TestValidate:
    def test_accepts_valid_config(self):
        _schema().validate({"client_id": "a", "client_secret": "b"})

    def test_missing_required_raises(self):
        with pytest.raises(InvalidConfigException):
            _schema().validate({"client_id": "a"})

    def test_optional_with_default_may_be_omitted(self):
        _schema().validate({"client_id": "a", "client_secret": "b"})

    def test_wrong_type_raises(self):
        with pytest.raises(InvalidConfigException):
            _schema().validate({"client_id": "a", "client_secret": "b", "timeout": "x"})

    def test_bool_is_not_an_integer(self):
        with pytest.raises(InvalidConfigException):
            _schema().validate(
                {"client_id": "a", "client_secret": "b", "timeout": True}
            )

    def test_collects_all_errors(self):
        with pytest.raises(InvalidConfigException) as exc:
            _schema().validate({"timeout": "x"})
        msg = str(exc.value)
        assert "client_id" in msg and "client_secret" in msg and "timeout" in msg

    def test_extra_keys_ignored(self):
        _schema().validate({"client_id": "a", "client_secret": "b", "extra": "x"})

    def test_empty_schema_is_permissive(self):
        ConfigSchema().validate({"anything": 1})


class TestJsonSchema:
    def test_shape_and_required(self):
        js = _schema().to_json_schema()
        assert js["type"] == "object"
        assert set(js["required"]) == {"client_id", "client_secret"}
        assert js["properties"]["timeout"]["type"] == "integer"
        assert js["properties"]["timeout"]["default"] == 30

    def test_secret_field_marked_write_only(self):
        js = _schema().to_json_schema()
        assert js["properties"]["client_secret"]["writeOnly"] is True
        assert "writeOnly" not in js["properties"]["client_id"]

    def test_json_safe(self):
        js = _schema().to_json_schema()
        assert json.loads(json.dumps(js)) == js


def test_secret_fields():
    assert _schema().secret_fields() == ("client_secret",)


def test_label_defaults_to_humanized_name():
    assert ConfigField("client_id").label == "Client Id"
    assert ConfigField("client_id", display_name="Client ID").label == "Client ID"


class TestValidation:
    def test_pattern_enforced(self):
        schema = ConfigSchema(
            (
                ConfigField(
                    "client_id",
                    pattern=r"\.example\.com$",
                    pattern_error="must end with .example.com",
                ),
            )
        )
        schema.validate({"client_id": "app.example.com"})
        with pytest.raises(InvalidConfigException) as exc:
            schema.validate({"client_id": "nope"})
        assert "must end with .example.com" in str(exc.value)

    def test_select_value_must_be_an_option(self):
        schema = ConfigSchema(
            (
                ConfigField(
                    "region",
                    required=False,
                    options=(ConfigOption("us"), ConfigOption("eu")),
                ),
            )
        )
        schema.validate({"region": "us"})
        with pytest.raises(InvalidConfigException):
            schema.validate({"region": "mars"})

    def test_list_multiselect_each_value_must_be_an_option(self):
        schema = ConfigSchema(
            (
                ConfigField(
                    "caps",
                    type=ConfigType.LIST,
                    required=False,
                    options=(ConfigOption("a"), ConfigOption("b")),
                ),
            )
        )
        schema.validate({"caps": ["a", "b"]})
        with pytest.raises(InvalidConfigException):
            schema.validate({"caps": ["a", "z"]})

    def test_default_template_satisfies_required(self):
        schema = ConfigSchema(
            (ConfigField("redirect_uri", default_template="{origin}/cb"),)
        )
        schema.validate({})


class TestScopeFields:
    def _schema(self) -> ConfigSchema:
        return ConfigSchema(
            (
                ConfigField("client_id"),
                ConfigField(
                    "scopes",
                    type=ConfigType.LIST,
                    out=ConfigOutput.SCOPES,
                    options=(ConfigOption("read", default_selected=True),),
                ),
            )
        )

    def test_scope_field_not_validated_as_config(self):
        self._schema().validate({"client_id": "a"})

    def test_scope_field_names(self):
        assert self._schema().scope_field_names() == ("scopes",)

    def test_scope_field_excluded_from_json_schema(self):
        js = self._schema().to_json_schema()
        assert "scopes" not in js["properties"]
        assert "client_id" in js["properties"]


class TestFormSpec:
    def _rich(self) -> ConfigSchema:
        return ConfigSchema(
            (
                ConfigField(
                    "client_id",
                    display_name="Client ID",
                    description="from console",
                    placeholder="x.apps",
                    importance=Importance.HIGH,
                    group="Credentials",
                    order=1,
                    pattern=r"\.apps$",
                ),
                ConfigField(
                    "scopes",
                    type=ConfigType.LIST,
                    required=False,
                    out=ConfigOutput.SCOPES,
                    group="Permissions",
                    order=2,
                    options=(
                        ConfigOption(
                            "gmail",
                            label="Gmail",
                            description="read mail",
                            default_selected=True,
                        ),
                        ConfigOption("drive", label="Drive"),
                    ),
                ),
            )
        )

    def test_form_spec_carries_rich_metadata(self):
        spec = self._rich().to_form_spec()
        client_id, scopes = spec[0], spec[1]
        assert client_id["label"] == "Client ID"
        assert client_id["importance"] == "high"
        assert client_id["group"] == "Credentials"
        assert client_id["pattern"] == r"\.apps$"
        assert scopes["type"] == "list"
        assert scopes["out"] == "scopes"
        assert scopes["options"][0] == {
            "value": "gmail",
            "label": "Gmail",
            "description": "read mail",
            "default_selected": True,
        }

    def test_form_spec_round_trips(self):
        schema = self._rich()
        rebuilt = ConfigSchema.from_form_spec(schema.to_form_spec())
        assert rebuilt.to_form_spec() == schema.to_form_spec()

    def test_form_spec_json_safe(self):
        spec = self._rich().to_form_spec()
        assert json.loads(json.dumps(spec)) == spec


class TestJsonSchemaFromConfigFields:
    def test_list_config_field_becomes_array_with_enum(self):
        schema = ConfigSchema(
            (
                ConfigField(
                    "regions",
                    type=ConfigType.LIST,
                    required=False,
                    options=(ConfigOption("us"), ConfigOption("eu")),
                ),
            )
        )
        prop = schema.to_json_schema()["properties"]["regions"]
        assert prop["type"] == "array"
        assert prop["items"]["enum"] == ["us", "eu"]

    def test_select_carries_enum_and_string_pattern(self):
        schema = ConfigSchema(
            (
                ConfigField(
                    "tier",
                    required=False,
                    options=(ConfigOption("a"), ConfigOption("b")),
                ),
                ConfigField("host", pattern=r"\.example\.com$"),
            )
        )
        props = schema.to_json_schema()["properties"]
        assert props["tier"]["enum"] == ["a", "b"]
        assert props["host"]["pattern"] == r"\.example\.com$"


def test_from_form_spec_tolerates_unknown_enum_values():
    # Unknown type/importance/out strings fall back to the default members.
    field = ConfigSchema.from_form_spec(
        [{"name": "x", "type": "bogus", "importance": "nope", "out": "huh"}]
    ).fields[0]
    assert field.type is ConfigType.STRING
    assert field.importance is Importance.MEDIUM
    assert field.out is ConfigOutput.CONFIG


class TestJsonSchemaRoundTrip:
    def test_list_field_round_trips_through_json_schema(self):
        # H1: to_json_schema emits a LIST as {"type":"array"}; from_json_schema must
        # map it back to LIST (not STRING) and rebuild options from the enum.
        schema = ConfigSchema(
            (
                ConfigField(
                    "regions",
                    type=ConfigType.LIST,
                    required=False,
                    options=(ConfigOption("us"), ConfigOption("eu")),
                ),
            )
        )
        rebuilt = ConfigSchema.from_json_schema(schema.to_json_schema())
        assert rebuilt.fields[0].type is ConfigType.LIST
        rebuilt.validate({"regions": ["us"]})  # valid list no longer rejected
        with pytest.raises(InvalidConfigException):
            rebuilt.validate({"regions": ["mars"]})  # options recovered from enum


class TestVisibility:
    def _schema(self) -> ConfigSchema:
        return ConfigSchema(
            (
                ConfigField("mode", required=False),
                ConfigField(
                    "advanced_token",
                    required=True,
                    depends_on="mode",
                    visible_when=("advanced",),
                ),
            )
        )

    def test_hidden_required_field_is_not_required(self):
        # H2: required field hidden by its controller must not be falsely required.
        self._schema().validate({"mode": "basic"})

    def test_visible_required_field_is_enforced(self):
        with pytest.raises(InvalidConfigException):
            self._schema().validate({"mode": "advanced"})
        self._schema().validate({"mode": "advanced", "advanced_token": "t"})


class TestPatternHardening:
    def test_malformed_pattern_raises_typed_error_not_re_error(self):
        # H3: a bad manifest pattern is a config error, not an uncaught re.error/500.
        schema = ConfigSchema((ConfigField("x", pattern="(unclosed"),))
        with pytest.raises(InvalidConfigException):
            schema.validate({"x": "abc"})

    def test_overlong_value_rejected_before_running_regex(self):
        # H3: an over-length input is rejected up-front, so a ReDoS pattern never runs.
        schema = ConfigSchema((ConfigField("x", pattern=r"(a+)+$"),))
        with pytest.raises(InvalidConfigException):
            schema.validate({"x": "a" * 5000})


def test_list_rejects_non_string_elements():
    # M1: LIST items are strings in the emitted schema; enforce it.
    schema = ConfigSchema((ConfigField("tags", type=ConfigType.LIST, required=False),))
    with pytest.raises(InvalidConfigException):
        schema.validate({"tags": ["a", 1]})


def test_from_spec_rejects_malformed_spec():
    # M2: malformed spec -> typed error, not a raw KeyError/ValueError.
    with pytest.raises(InvalidConfigException):
        ConfigField.from_spec({})  # missing name
    with pytest.raises(InvalidConfigException):
        ConfigField.from_spec({"name": "x", "order": "nope"})  # non-int order


def test_json_schema_and_form_spec_cover_the_same_fields():
    # M3: config_schema (validation) and config_fields (form) must stay consistent --
    # the form carries every field; the JSON Schema carries exactly the non-scope ones.
    schema = ConfigSchema(
        (
            ConfigField("client_id"),
            ConfigField(
                "scopes",
                type=ConfigType.LIST,
                required=False,
                out=ConfigOutput.SCOPES,
                options=(ConfigOption("read"),),
            ),
        )
    )
    form_names = {f["name"] for f in schema.to_form_spec()}
    js_props = set(schema.to_json_schema()["properties"])
    scope_names = set(schema.scope_field_names())
    assert form_names == {"client_id", "scopes"}
    assert js_props == form_names - scope_names == {"client_id"}


def test_public_api_exports_the_form_types():
    # H4: connector authors must reach the form types from the package root.
    from kamiwaza_sdk.connectors import ConfigOption as Opt
    from kamiwaza_sdk.connectors import ConfigOutput as Out
    from kamiwaza_sdk.connectors import FieldWidth as Width
    from kamiwaza_sdk.connectors import Importance as Imp

    assert (Opt, Out, Width, Imp) == (
        ConfigOption,
        ConfigOutput,
        FieldWidth,
        Importance,
    )
