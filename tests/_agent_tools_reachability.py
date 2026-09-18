"""What an agent can reach through a client, computed without the index.

Two suites assert the index's coverage — the unit suite compares selector sets,
the contract suite compares sizes — and both need the same independent walk of
the client. One copy, so the two can only disagree with the index and never
with each other.

Deliberately written without using anything from ``kamiwaza_sdk.agent_tools``:
a check that shares its subject's code proves only that the code agrees with
itself.
"""

from __future__ import annotations

import inspect
from typing import Any


def public_methods(obj: Any) -> list[str]:
    """Every public method name on an object's type.

    Args:
        obj: The service or sub-client to inspect.

    Returns:
        Method names, without the private ones.
    """
    return [
        name
        for name, _ in inspect.getmembers(type(obj), predicate=inspect.isfunction)
        if not name.startswith("_")
    ]


def service_properties(client: Any) -> list[tuple[str, Any]]:
    """The client's service properties.

    Args:
        client: The platform client.

    Returns:
        ``(name, service)`` pairs, in declaration order.
    """
    return [
        (name, getattr(client, name))
        for name, value in vars(type(client)).items()
        if isinstance(value, property) and not name.startswith("_")
    ]


def nested_sub_clients(service: Any) -> list[tuple[str, Any]]:
    """The attributes of a service that hold a platform client of their own.

    This is how ``catalog.secrets`` and ``gates.packages`` are reached. A plain
    helper object one level down holds no client of its own, and is not an
    operation family.

    Args:
        service: A service read from a client property.

    Returns:
        ``(attribute, sub_client)`` pairs.
    """
    found: list[tuple[str, Any]] = []
    for attribute in dir(service):
        if attribute.startswith("_") or attribute == "client":
            continue
        nested = getattr(service, attribute, None)
        if nested is None or not type(nested).__module__.startswith("kamiwaza"):
            continue
        if not hasattr(nested, "client") and not hasattr(nested, "_client"):
            continue
        found.append((attribute, nested))
    return found


def reachable_selectors(client: Any) -> set[str]:
    """Every selector an agent could call through this client.

    Args:
        client: The platform client.

    Returns:
        Selectors as ``service.method`` and ``service.sub_client.method``.
    """
    selectors: set[str] = set()
    for name, service in service_properties(client):
        selectors.update(f"{name}.{method}" for method in public_methods(service))
        for attribute, nested in nested_sub_clients(service):
            selectors.update(
                f"{name}.{attribute}.{method}" for method in public_methods(nested)
            )
    return selectors
