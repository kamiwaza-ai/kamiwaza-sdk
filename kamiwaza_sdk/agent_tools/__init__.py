"""Agent-facing tool descriptors for the Kamiwaza platform API.

Converts the platform's operations into tool descriptors — published ids, input
and output schemas, annotations, categories, and the operation index used to
search them — dispatched through the existing ``KamiwazaClient`` service layer.

The requirements this layer serves are specified outside this package, in the
``kamiwaza-mcp`` repository at ``specs/001-mcp-server/``. That repository holds
the MCP server; this package holds what the server publishes.

Naming
------
The package is ``agent_tools`` rather than ``tools`` on purpose:
``kamiwaza_sdk.services.tools.ToolService`` (deprecated in favour of
``ExtensionService``) already means "deploy a third-party MCP tool server on the
Kamiwaza platform", which is the inverse of this work. Do not reuse that name.

Protocol neutrality (load-bearing)
----------------------------------
This package MUST NOT import an MCP library, encode an MCP wire shape, or take
an MCP dependency of any kind. Descriptors are protocol-neutral data: a name, a
description, JSON Schema for input and output, an effect classification, and an
annotation set. The server maps them onto the protocol.

Three reasons, in order of how much they cost to get wrong:

1. Every consumer of this SDK would inherit an MCP dependency it does not use.
2. The MCP specification revises on its own schedule. A wire shape baked in here
   turns a protocol revision into an SDK release.
3. A descriptor is useful to a caller building a custom agent with no MCP in
   sight, which is the whole reason this layer ships separately from the server.

Where a requirement is genuinely about the wire — task handles, awaiting-input
results, capability negotiation, authorization challenges — it belongs to the
server, not here.

Layout as this fills in
-----------------------
- ``descriptors``   published ids, strict input schemas, output schemas derived
  from the existing service models, annotations, and categories.
- ``envelopes``     the structured result and failure payloads, including the
  five outcome kinds, as plain data.
- ``workflows``     hand-written multi-call workflow tools composed from the
  service layer, each naming one polling step and its resume behaviour.
- ``spec_index``    the searchable operation index built from the committed
  ``kamiwaza-openapi-spec.json``; queried on demand, never emitted in bulk.
- ``catalog``       the full per-operation descriptor set, categorised and
  filterable, for a consumer that deliberately wants it.
"""

__all__: tuple[str, ...] = ()
