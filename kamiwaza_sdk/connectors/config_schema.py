"""Declarative connector config contract.

A connector declares the admin configuration it needs as a :class:`ConfigSchema`
(an ordered set of :class:`ConfigField`). The platform derives the whole admin form,
its validation, and a JSON Schema from that one declaration -- so the config UI is
written once, *in the connector*, and core renders it generically with no
connector-specific UI code.

Each :class:`ConfigField` carries enough metadata for a faithful form:

- a value ``type`` (incl. multi-value ``LIST``) and a write-only ``secret`` flag;
- a ``label`` + ``description`` (help text) and a ``placeholder``;
- ``group`` + ``order`` for layout and ``importance`` for prominence;
- optional validation: a regex ``pattern`` (+ ``pattern_error``) and ``required``;
- recommended/allowed ``options`` -- each with its own label -- which render a
  select (single value) or a multi-select (a ``LIST`` field);
- a static ``default`` or a ``default_template`` resolved by the renderer
  (e.g. ``"{origin}/api/connectors/public/{connector_type}/callback"``);
- a simple visibility rule (``depends_on`` + ``visible_when``) so a field can show
  only when another field has a given value.

A field's value routes to the connector ``config`` by default, or to the OAuth
``scopes`` set when ``out`` says so -- which is how a connector offers selectable
permissions (the admin ticks capabilities; the ticked values become the requested
scopes) without any of that living in core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .exceptions import InvalidConfigException


class ConfigType(str, Enum):
    """The value type of a config field."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    LIST = "list"  # multi-value; rendered as a multi-select when ``options`` are given


class Importance(str, Enum):
    """How prominently the admin form should surface a field."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FieldWidth(str, Enum):
    """A hint for how wide the input should render."""

    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class ConfigOutput(str, Enum):
    """Where a field's value goes when the admin saves the form."""

    CONFIG = "config"  # -> the connector's stored config (the default)
    SCOPES = "scopes"  # -> the OAuth scopes requested (selectable permissions)


_PY_TYPES: dict[ConfigType, type] = {
    ConfigType.STRING: str,
    ConfigType.INTEGER: int,
    ConfigType.BOOLEAN: bool,
    ConfigType.LIST: list,
}


def _humanize(name: str) -> str:
    """A default display label from a field name (``client_id`` -> ``Client Id``)."""
    return name.replace("_", " ").replace("-", " ").strip().title()


@dataclass(frozen=True)
class ConfigOption:
    """One recommended/allowed value for a field (a select / multi-select choice)."""

    value: str
    label: str = ""
    description: str = ""
    default_selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "label": self.label or self.value,
            "description": self.description,
            "default_selected": self.default_selected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfigOption:
        return cls(
            value=str(data["value"]),
            label=data.get("label", ""),
            description=data.get("description", ""),
            default_selected=bool(data.get("default_selected", False)),
        )


@dataclass(frozen=True)
class ConfigField:
    """One admin-config field a connector declares, with its full rendering metadata.

    ``required`` with no ``default`` / ``default_template`` means the admin must
    supply it. ``secret`` marks credential material (rendered write-only, routed to
    the secret store). ``options`` make the field a select (or a multi-select when
    ``type`` is ``LIST``). ``out`` routes a ``LIST`` field's selections to the OAuth
    scope set instead of the config blob.
    """

    name: str
    type: ConfigType = ConfigType.STRING
    required: bool = True
    secret: bool = False
    default: Any = None
    description: str = ""
    display_name: str = ""
    placeholder: str = ""
    importance: Importance = Importance.MEDIUM
    group: str = ""
    order: int = 0
    width: FieldWidth = FieldWidth.MEDIUM
    pattern: str = ""
    pattern_error: str = ""
    options: tuple[ConfigOption, ...] = ()
    default_template: str = ""
    depends_on: str = ""
    visible_when: tuple[str, ...] = ()
    out: ConfigOutput = ConfigOutput.CONFIG

    @property
    def label(self) -> str:
        """The display label (declared ``display_name`` or a humanized name)."""
        return self.display_name or _humanize(self.name)

    @property
    def must_supply(self) -> bool:
        """Whether the admin must provide a value (required, no default of any kind).

        A directly-declared ``required=True`` field that *also* carries a ``default``
        (or ``default_template``) is therefore NOT must-supply -- the default satisfies
        it. JSON-Schema ``required`` is stronger intent, so :meth:`ConfigSchema.from_json_schema`
        drops the default for a required field to keep it must-supply.
        """
        return self.required and self.default is None and not self.default_template

    def _allowed(self) -> set[str]:
        return {opt.value for opt in self.options}

    def to_spec(self) -> dict[str, Any]:
        """The rich, ordered spec the admin form renderer is generated from."""
        return {
            "name": self.name,
            "type": self.type.value,
            "required": self.required,
            "secret": self.secret,
            "default": self.default,
            "default_template": self.default_template,
            "description": self.description,
            "label": self.label,
            "placeholder": self.placeholder,
            "importance": self.importance.value,
            "group": self.group,
            "order": self.order,
            "width": self.width.value,
            "pattern": self.pattern,
            "pattern_error": self.pattern_error,
            "options": [opt.to_dict() for opt in self.options],
            "depends_on": self.depends_on,
            "visible_when": list(self.visible_when),
            "out": self.out.value,
        }

    @classmethod
    def from_spec(cls, data: dict[str, Any]) -> ConfigField:
        """Reconstruct a field from a spec produced by :meth:`to_spec`.

        Fails with :class:`InvalidConfigException` (not a raw KeyError/ValueError) on a
        malformed spec -- a missing ``name``/option ``value`` or a non-numeric
        ``order`` -- consistent with the forgiving enum handling.
        """
        try:
            return cls(
                name=data["name"],
                type=_enum(ConfigType, data.get("type"), ConfigType.STRING),
                required=bool(data.get("required", True)),
                secret=bool(data.get("secret", False)),
                default=data.get("default"),
                description=data.get("description", ""),
                display_name=data.get("label", ""),
                placeholder=data.get("placeholder", ""),
                importance=_enum(Importance, data.get("importance"), Importance.MEDIUM),
                group=data.get("group", ""),
                order=int(data.get("order", 0) or 0),
                width=_enum(FieldWidth, data.get("width"), FieldWidth.MEDIUM),
                pattern=data.get("pattern", ""),
                pattern_error=data.get("pattern_error", ""),
                options=tuple(
                    ConfigOption.from_dict(o) for o in (data.get("options") or [])
                ),
                default_template=data.get("default_template", ""),
                depends_on=data.get("depends_on", ""),
                visible_when=tuple(data.get("visible_when") or ()),
                out=_enum(ConfigOutput, data.get("out"), ConfigOutput.CONFIG),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidConfigException(f"malformed config field spec: {exc}") from exc


def _enum(enum_cls: type, value: Any, fallback: Any) -> Any:
    """Coerce a serialized value back to its enum member, falling back if unknown."""
    try:
        return enum_cls(value)
    except ValueError:
        return fallback


# Cap the input length a manifest-supplied regex runs against. The pattern comes from
# the connector manifest and the value from admin input; bounding the length blunts
# catastrophic-backtracking (ReDoS) blowup on a hostile/buggy pattern.
# ponytail: length cap, not a regex timeout -- add a timeout if untrusted manifests
# ever ship patterns.
_MAX_PATTERN_INPUT = 1024


def _pattern_errors(field: ConfigField, value: str) -> list[str]:
    """Apply a field's regex, failing closed on a too-long input or bad pattern.

    A malformed manifest pattern raises ``re.error``; surface it as a config error
    rather than letting a raw ``re.error`` escape as an uncaught 500. ``re.search`` is
    unanchored -- it matches a substring unless the pattern anchors with ``^``/``$``.
    """
    if len(value) > _MAX_PATTERN_INPUT:
        return [f"config {field.name} is too long to validate (>{_MAX_PATTERN_INPUT})"]
    try:
        matched = re.search(field.pattern, value) is not None
    except re.error:
        return [f"config {field.name} has an invalid validation pattern"]
    if not matched:
        return [
            field.pattern_error
            or f"config {field.name} does not match the required format"
        ]
    return []


@dataclass(frozen=True)
class ConfigSchema:
    """A connector's declarative admin-config contract (one ordered field set)."""

    fields: tuple[ConfigField, ...] = ()

    def _config_fields(self) -> tuple[ConfigField, ...]:
        """Fields whose value is stored as connector config (not OAuth scopes)."""
        return tuple(f for f in self.fields if f.out is ConfigOutput.CONFIG)

    def scope_field_names(self) -> tuple[str, ...]:
        """Names of fields whose selected values are OAuth scopes, not config."""
        return tuple(f.name for f in self.fields if f.out is ConfigOutput.SCOPES)

    def secret_fields(self) -> tuple[str, ...]:
        """Names of fields holding secrets (for redaction / secret-store routing)."""
        return tuple(f.name for f in self.fields if f.secret)

    def validate(self, config: dict[str, Any]) -> None:
        """Validate admin config against the schema; raise once with all problems.

        Unknown keys are ignored (forward-compatible). Scope-routed fields are not
        part of the config blob, so they are not validated here.
        """
        errors: list[str] = []
        for field in self._config_fields():
            # A field gated by depends_on/visible_when is only required/validated when
            # active, so a conditionally-required field hidden by its controller is not
            # falsely required (matches what the form renderer shows).
            if not self._is_active(field, config):
                continue
            value = config.get(field.name)
            if value is None:
                if field.must_supply:
                    errors.append(f"missing required config: {field.name}")
                continue
            expected = _PY_TYPES[field.type]
            # bool is a subclass of int -- never accept a bool for an integer field.
            if not isinstance(value, expected) or (
                expected is int and isinstance(value, bool)
            ):
                errors.append(f"config {field.name} must be {field.type.value}")
                continue
            errors.extend(self._value_errors(field, value))
        if errors:
            raise InvalidConfigException("; ".join(errors))

    @staticmethod
    def _is_active(field: ConfigField, config: dict[str, Any]) -> bool:
        """Whether a field is shown (and thus required/validated) given visibility.

        No ``depends_on`` -> always active. Otherwise active only when the controlling
        field's value is one of ``visible_when`` -- the same rule the form renderer
        applies, so validate() never requires a field the admin cannot see.
        """
        if not field.depends_on:
            return True
        return str(config.get(field.depends_on)) in field.visible_when

    @staticmethod
    def _value_errors(field: ConfigField, value: Any) -> list[str]:
        """Per-value checks (element types, allowed options, regex) once type is right."""
        errors: list[str] = []
        candidates = value if field.type is ConfigType.LIST else [value]
        # The emitted schema declares LIST items as strings; enforce that so a list
        # value can't smuggle non-strings past validation.
        if field.type is ConfigType.LIST and any(
            not isinstance(v, str) for v in candidates
        ):
            return [f"config {field.name} must be a list of strings"]
        if field.options:
            allowed = field._allowed()
            bad = [str(v) for v in candidates if str(v) not in allowed]
            if bad:
                # Bound the echo so a large/hostile input can't bloat the message.
                errors.append(f"config {field.name} has invalid value(s): {bad[:5]}")
        if field.pattern and field.type is ConfigType.STRING:
            errors.extend(_pattern_errors(field, value))
        return errors

    def to_form_spec(self) -> list[dict[str, Any]]:
        """The ordered rich field specs the admin form is rendered from."""
        return [field.to_spec() for field in self.fields]

    @classmethod
    def from_form_spec(cls, specs: list[dict[str, Any]] | None) -> ConfigSchema:
        """Reconstruct a schema from the rich field specs in a connector manifest."""
        return cls(fields=tuple(ConfigField.from_spec(s) for s in (specs or ())))

    def to_json_schema(self) -> dict[str, Any]:
        """Render a JSON Schema (validation-faithful; scope fields excluded).

        Standard keywords carry the validatable shape (type / required / enum /
        pattern / default / writeOnly); the rich rendering metadata travels
        separately as the form spec (:meth:`to_form_spec`).
        """
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field in self._config_fields():
            values = [opt.value for opt in field.options]
            if field.type is ConfigType.LIST:
                items: dict[str, Any] = {"type": "string"}
                if values:
                    items["enum"] = values
                prop: dict[str, Any] = {"type": "array", "items": items}
            else:
                prop = {"type": field.type.value}
                if values:
                    prop["enum"] = values
            if field.description:
                prop["description"] = field.description
            if field.default is not None:
                prop["default"] = field.default
            if field.pattern and field.type is ConfigType.STRING:
                prop["pattern"] = field.pattern
            if field.secret:
                prop["writeOnly"] = True
            properties[field.name] = prop
            if field.must_supply:
                required.append(field.name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    @classmethod
    def from_json_schema(cls, schema: dict[str, Any]) -> ConfigSchema:
        """Rebuild a (basic) ConfigSchema from a JSON Schema.

        For a connector that ships only the legacy JSON Schema (no form spec), this
        recovers enough to validate: required, type, secret, default, description,
        pattern, and (from ``enum``) the allowed options. Prefer :meth:`from_form_spec`
        when the manifest carries the rich field specs.
        """
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or ())
        # JSON Schema spells a LIST field "array"; map it back so a LIST round-trips
        # (to_json_schema emits "array") instead of collapsing to STRING and then
        # rejecting valid list values.
        type_by_value = {t.value: t for t in ConfigType}
        type_by_value["array"] = ConfigType.LIST

        def _resolve_type(raw: Any) -> ConfigType:
            # Tolerate a nullable type array (e.g. ["integer", "null"]) and any
            # non-string type node by falling back to STRING.
            if isinstance(raw, list):
                raw = next((t for t in raw if t != "null"), "string")
            if not isinstance(raw, str):
                return ConfigType.STRING
            return type_by_value.get(raw, ConfigType.STRING)

        def _options(prop: dict[str, Any]) -> tuple[ConfigOption, ...]:
            # enum lives on the field for a scalar select, or on items for an array.
            enum = prop.get("enum") or (prop.get("items") or {}).get("enum") or ()
            return tuple(ConfigOption(str(v)) for v in enum)

        fields = tuple(
            ConfigField(
                name=name,
                type=_resolve_type((prop or {}).get("type", "string")),
                required=name in required,
                secret=bool((prop or {}).get("writeOnly")),
                # `required` wins over a declared default: a required field stays
                # must-supply, so its default can't suppress the missing-field check.
                default=None if name in required else (prop or {}).get("default"),
                description=(prop or {}).get("description", ""),
                pattern=(prop or {}).get("pattern", ""),
                options=_options(prop or {}),
            )
            for name, prop in properties.items()
        )
        return cls(fields=fields)
