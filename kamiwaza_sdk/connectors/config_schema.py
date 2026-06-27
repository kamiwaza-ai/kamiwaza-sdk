"""Declarative connector config contract -- the framework's ConfigDef analog.

A connector declares the admin configuration it needs as a :class:`ConfigSchema`
(a set of :class:`ConfigField`). The framework derives BOTH validation and the
admin-form JSON Schema from that one declaration, and marks secret fields so the
UI and secret store can protect them. So a connector author writes the config once
and gets validation + UI for free -- the Kafka Connect ``ConfigDef`` idea adapted
to Kamiwaza connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .exceptions import InvalidConfigException


class ConfigType(str, Enum):
    """The value type of a config field (kept small and JSON-Schema-mappable)."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"


_PY_TYPES: dict[ConfigType, type] = {
    ConfigType.STRING: str,
    ConfigType.INTEGER: int,
    ConfigType.BOOLEAN: bool,
}


@dataclass(frozen=True)
class ConfigField:
    """One admin-config field a connector declares.

    ``required`` with no ``default`` means the admin must supply it. ``secret``
    marks credential material (rendered write-only, routed to the secret store).
    """

    name: str
    type: ConfigType = ConfigType.STRING
    required: bool = True
    secret: bool = False
    default: Any = None
    description: str = ""

    @property
    def must_supply(self) -> bool:
        """Whether the admin must provide a value (required and no default)."""
        return self.required and self.default is None


@dataclass(frozen=True)
class ConfigSchema:
    """A connector's declarative admin-config contract (the ConfigDef analog)."""

    fields: tuple[ConfigField, ...] = ()

    def validate(self, config: dict[str, Any]) -> None:
        """Validate admin config against the schema; raise with all problems.

        Unknown keys are ignored (forward-compatible). Collects every error and
        raises once, so the admin sees all problems at a time.
        """
        errors: list[str] = []
        for field in self.fields:
            value = config.get(field.name)
            if value is None:
                if field.must_supply:
                    errors.append(f"missing required config: {field.name}")
                continue
            expected = _PY_TYPES[field.type]
            # bool is a subclass of int -- never accept a bool for an integer field
            if not isinstance(value, expected) or (
                expected is int and isinstance(value, bool)
            ):
                errors.append(f"config {field.name} must be {field.type.value}")
        if errors:
            raise InvalidConfigException("; ".join(errors))

    @classmethod
    def from_json_schema(cls, schema: dict[str, Any]) -> "ConfigSchema":
        """Rebuild a ConfigSchema from a JSON Schema (the inverse of to_json_schema).

        A *remote* connector ships its config contract as JSON Schema in its manifest;
        core must validate admin config against that, not the empty base schema, so a
        missing required field (e.g. a confidential connector's client_secret) is
        rejected. Reconstruction is validation-faithful: a field listed in ``required``
        becomes must-supply; ``writeOnly`` marks secrets; an unknown type falls back to
        string.
        """
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or ())
        type_by_value = {t.value: t for t in ConfigType}
        fields = tuple(
            ConfigField(
                name=name,
                type=type_by_value.get((prop or {}).get("type", "string"), ConfigType.STRING),
                required=name in required,
                secret=bool((prop or {}).get("writeOnly")),
                # A property listed in JSON Schema ``required`` must be supplied —
                # ``required`` wins over a ``default``. Carrying the default would
                # make ``must_supply`` false and let ``validate({})`` accept a
                # missing required field from an externally-authored manifest.
                default=None if name in required else (prop or {}).get("default"),
                description=(prop or {}).get("description", ""),
            )
            for name, prop in properties.items()
        )
        return cls(fields=fields)

    def to_json_schema(self) -> dict[str, Any]:
        """Render a JSON Schema the admin UI auto-renders the config form from."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field in self.fields:
            prop: dict[str, Any] = {"type": field.type.value}
            if field.description:
                prop["description"] = field.description
            if field.default is not None:
                prop["default"] = field.default
            if field.secret:
                prop["writeOnly"] = True
            properties[field.name] = prop
            if field.must_supply:
                required.append(field.name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def secret_fields(self) -> tuple[str, ...]:
        """Names of fields holding secrets (for redaction / secret-store routing)."""
        return tuple(field.name for field in self.fields if field.secret)
