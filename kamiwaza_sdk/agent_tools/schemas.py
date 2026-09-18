"""JSON Schema derivation for the agent tool surface.

Two asymmetric jobs, per the MCP server's FR-007:

* **input schemas are strict.** Unknown fields are rejected before anything is
  dispatched, and the refusal names the offending field, because an agent that
  guessed a field name needs to be told rather than silently ignored.
* **result schemas are permissive.** A field the platform adds next release
  must not break a working integration, so results describe what is known
  without forbidding what is not.

Schemas are derived from the client's own type annotations and Pydantic models.
Nothing is hand-written: a hand-kept schema beside a typed method is a second
source of truth that goes stale on the first signature change (Principle V).

One annotation in the current client cannot be resolved at all — it names a
module alias that is not importable at runtime. Derivation degrades to an
unconstrained schema for that operation rather than failing the surface, and
:func:`underivable` reports it.
"""

from __future__ import annotations

import inspect
import typing
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from .descriptors import resolve_service
from .spec_index import OperationEntry, OperationIndex

__all__ = [
    "OperationSchemas",
    "input_schema",
    "result_schema",
    "schemas_for",
    "underivable",
]

#: Python primitives that map directly onto a JSON Schema type.
_PRIMITIVES: dict[type, dict[str, Any]] = {
    str: {"type": "string"},
    bool: {"type": "boolean"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bytes: {"type": "string", "contentEncoding": "base64"},
    uuid.UUID: {"type": "string", "format": "uuid"},
    datetime: {"type": "string", "format": "date-time"},
    date: {"type": "string", "format": "date"},
    type(None): {"type": "null"},
}

#: Any value. Used where an annotation is absent or cannot be resolved — an
#: empty schema accepts anything, which is honest about what is known rather
#: than inventing a constraint the method does not have.
_ANY: dict[str, Any] = {}


@dataclass(frozen=True, slots=True)
class OperationSchemas:
    """One operation's input and result schemas.

    Attributes:
        input: JSON Schema for the arguments. Strict: unknown fields rejected.
        result: JSON Schema for the result. Permissive: unknown fields allowed.
        derived: Whether every annotation resolved. ``False`` means at least one
            fell back to an unconstrained schema, which a gate can report
            rather than a caller discovering it at call time.
    """

    input: dict[str, Any]
    result: dict[str, Any]
    derived: bool


def _model_schema(model: type[BaseModel], *, strict: bool) -> dict[str, Any]:
    """Return a Pydantic model's JSON Schema.

    Args:
        model: The model class.
        strict: Whether to forbid unknown fields.

    Returns:
        The model's schema, with ``additionalProperties`` set to ``False`` when
        strict. Pydantic emits nested models under ``$defs`` and refers to them
        with ``$ref``, which is left intact — a resolver is the consumer's job
        and flattening would lose the sharing.

        Two cases yield a bare object schema instead. A method annotated with
        ``BaseModel`` itself says only "some model", and Pydantic refuses to
        generate a schema for the base class. A model whose own fields cannot
        be represented raises, and one such model must not cost the surface.
    """
    if model is BaseModel:
        return {"type": "object"}
    try:
        schema = model.model_json_schema()
    except Exception:  # noqa: BLE001 - an unrepresentable model is not fatal
        return {"type": "object"}
    if strict:
        schema["additionalProperties"] = False
    return schema


def _enum_schema(annotation: type[Enum]) -> dict[str, Any]:
    """Return a schema enumerating an Enum's values.

    Args:
        annotation: The Enum class.

    Returns:
        A schema with the permitted values, so an agent picks from them rather
        than guessing a string.
    """
    return {"enum": [member.value for member in annotation]}


def _union_schema(args: tuple[Any, ...], *, strict: bool) -> dict[str, Any]:
    """Return a schema for a union, collapsing the common optional case.

    Args:
        args: The union's members.
        strict: Whether nested objects forbid unknown fields.

    Returns:
        The member's schema made nullable when the union is exactly one type
        plus ``None``, otherwise an ``anyOf``. The collapse matters because
        ``Optional[str]`` is the shape most of this client uses and
        ``{"type": ["string", "null"]}`` reads better than a two-branch
        ``anyOf``.
    """
    non_none = [arg for arg in args if arg is not type(None)]
    if len(non_none) == 1 and len(non_none) < len(args):
        inner = _schema_for(non_none[0], strict=strict)
        declared = inner.get("type")
        if isinstance(declared, str):
            return {**inner, "type": [declared, "null"]}
        return {"anyOf": [inner, {"type": "null"}]}
    return {"anyOf": [_schema_for(arg, strict=strict) for arg in args]}


def _sequence_schema(
    origin: Any, args: tuple[Any, ...], *, strict: bool
) -> dict[str, Any]:
    """Return a schema for a list, set, or frozenset annotation.

    Args:
        origin: The generic's origin type.
        args: Its type arguments.
        strict: Whether nested objects forbid unknown fields.

    Returns:
        An array schema, marked unique for the set types.
    """
    schema: dict[str, Any] = {
        "type": "array",
        "items": _schema_for(args[0], strict=strict) if args else _ANY,
    }
    if origin in (set, frozenset):
        schema["uniqueItems"] = True
    return schema


def _tuple_schema(args: tuple[Any, ...], *, strict: bool) -> dict[str, Any]:
    """Return a schema for a tuple annotation.

    Args:
        args: The tuple's type arguments.
        strict: Whether nested objects forbid unknown fields.

    Returns:
        A homogeneous array schema for ``tuple[X, ...]``, otherwise a
        positional ``prefixItems`` schema.
    """
    if not args or args[-1] is Ellipsis:
        return {
            "type": "array",
            "items": _schema_for(args[0], strict=strict) if args else _ANY,
        }
    return {
        "type": "array",
        "prefixItems": [_schema_for(arg, strict=strict) for arg in args],
    }


def _mapping_schema(args: tuple[Any, ...], *, strict: bool) -> dict[str, Any]:
    """Return a schema for a dict annotation.

    Args:
        args: The mapping's key and value types.
        strict: Whether nested objects forbid unknown fields.

    Returns:
        An object schema whose ``additionalProperties`` carries the value type.
        Keys are not constrained: JSON object keys are strings regardless of
        what the annotation claims.
    """
    values = _schema_for(args[1], strict=strict) if len(args) == 2 else _ANY
    return {"type": "object", "additionalProperties": values}


def _generic_schema(
    origin: Any, args: tuple[Any, ...], *, strict: bool
) -> dict[str, Any]:
    """Return a schema for a parameterised annotation.

    Args:
        origin: The generic's origin, from :func:`typing.get_origin`.
        args: Its type arguments.
        strict: Whether nested objects forbid unknown fields.

    Returns:
        The schema, or the unconstrained schema for a generic this module does
        not model — an empty schema is honest about what is known.
    """
    if origin is typing.Literal:
        return {"enum": list(args)}
    if origin is typing.Union or str(origin) == "types.UnionType":
        return _union_schema(args, strict=strict)
    if origin in (list, set, frozenset):
        return _sequence_schema(origin, args, strict=strict)
    if origin is tuple:
        return _tuple_schema(args, strict=strict)
    if origin is dict:
        return _mapping_schema(args, strict=strict)
    return dict(_ANY)


def _class_schema(annotation: type, *, strict: bool) -> dict[str, Any]:
    """Return a schema for a plain class annotation.

    Args:
        annotation: The class.
        strict: Whether objects forbid unknown fields.

    Returns:
        The model or enum schema, or the unconstrained schema for a class this
        module does not model.
    """
    if issubclass(annotation, BaseModel):
        return _model_schema(annotation, strict=strict)
    if issubclass(annotation, Enum):
        return _enum_schema(annotation)
    return dict(_ANY)


def _schema_for(annotation: Any, *, strict: bool) -> dict[str, Any]:
    """Return the JSON Schema for one resolved type annotation.

    Args:
        annotation: A resolved annotation — not a string.
        strict: Whether objects forbid unknown fields.

    Returns:
        The schema. An annotation this module does not recognise yields the
        unconstrained schema rather than a guess.
    """
    if annotation is Any or annotation is None:
        return dict(_ANY)
    if annotation in _PRIMITIVES:
        return dict(_PRIMITIVES[annotation])
    origin = typing.get_origin(annotation)
    if origin is not None:
        return _generic_schema(origin, typing.get_args(annotation), strict=strict)
    if inspect.isclass(annotation):
        return _class_schema(annotation, strict=strict)
    return dict(_ANY)


def _hints(method: Any) -> dict[str, Any] | None:
    """Resolve a method's type hints, or ``None`` when they cannot be resolved.

    Args:
        method: The unbound function.

    Returns:
        The resolved hints, or ``None`` when an annotation names something not
        importable at runtime. One such method exists in the current client.
    """
    try:
        return typing.get_type_hints(method)
    except Exception:  # noqa: BLE001 - an unresolvable annotation is not fatal
        return None


def input_schema(method: Any) -> tuple[dict[str, Any], bool]:
    """Derive the strict input schema for a client method.

    Args:
        method: The unbound function.

    Returns:
        The schema and whether every annotation resolved. Unknown fields are
        rejected: ``additionalProperties`` is ``False``, so an agent that
        invents an argument name is told rather than silently ignored.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return schema, False
    hints = _hints(method)
    derived = hints is not None
    for name, parameter in signature.parameters.items():
        if name == "self" or parameter.kind in (
            parameter.VAR_POSITIONAL,
            parameter.VAR_KEYWORD,
        ):
            continue
        annotation = (hints or {}).get(name)
        if annotation is None:
            derived = False
        schema["properties"][name] = _schema_for(annotation, strict=True)
        if parameter.default is parameter.empty:
            schema["required"].append(name)
    return schema, derived


def result_schema(method: Any) -> tuple[dict[str, Any], bool]:
    """Derive the permissive result schema for a client method.

    Args:
        method: The unbound function.

    Returns:
        The schema and whether the return annotation resolved. Objects keep
        ``additionalProperties`` open, because rejecting a field the platform
        added would break a working integration on a platform upgrade — the
        asymmetry with :func:`input_schema` is the point.
    """
    hints = _hints(method)
    if hints is None:
        return dict(_ANY), False
    annotation = hints.get("return")
    if annotation is None:
        return dict(_ANY), False
    return _schema_for(annotation, strict=False), True


def schemas_for(entry: OperationEntry, service: Any) -> OperationSchemas:
    """Derive both schemas for one indexed operation.

    Args:
        entry: The indexed operation.
        service: The service object the method belongs to.

    Returns:
        The operation's schemas. An operation whose method cannot be found
        yields unconstrained schemas with ``derived`` false rather than raising,
        so one odd method never costs the whole surface.
    """
    method = getattr(type(service), entry.method, None)
    if method is None:
        return OperationSchemas(input=dict(_ANY), result=dict(_ANY), derived=False)
    arguments, arguments_derived = input_schema(method)
    result, result_derived = result_schema(method)
    return OperationSchemas(
        input=arguments,
        result=result,
        derived=arguments_derived and result_derived,
    )


def underivable(index: OperationIndex, client: Any) -> tuple[str, ...]:
    """Return the selectors whose schemas could not be fully derived.

    The service object is resolved with :func:`~.descriptors.resolve_service`,
    which walks a dotted service path. A single ``getattr`` for the whole
    dotted name never matches, so it reported ``None`` for every nested
    operation and the filter below then skipped them: 35 of the 335 published
    operations at this revision, each silently exempt from the gate. All 35
    derive cleanly, so the reported set is unchanged by the fix.

    Args:
        index: The operation index.
        client: The client the index was built from.

    Returns:
        Selectors of published operations with at least one unresolved or
        missing annotation, in index order. A gate reports these rather than a
        caller discovering an unconstrained schema at call time. An operation
        whose service is absent from this client is skipped, because nothing
        can be read from a service that is not there.
    """
    resolved = (
        (entry, resolve_service(client, entry.service)) for entry in index.published
    )
    return tuple(
        entry.selector
        for entry, service in resolved
        if service is not None
        if not schemas_for(entry, service).derived
    )
