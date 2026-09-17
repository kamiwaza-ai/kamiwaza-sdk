"""Federation, shared identity-provider, and ReBAC access CLI.

The ``kamiwaza-federation`` utility provides deterministic federation and
resource-access setup:

* ``access`` manages ReBAC grants on resources.
* ``fed`` manages the shared identity-provider federation lifecycle.
* ``dataset``, ``gate``, and ``attr`` configure attribute-gated retrieval.
* ``idp`` seeds Keycloak realms, clients, mappers, and users for development
  and test environments.

``idp bootstrap`` and ``idp persona`` call the Keycloak admin REST API, which
the platform ingress deliberately does not expose. They require direct Keycloak
access, such as a local port-forward. ``idp token`` uses the public ROPC flow.
Production identity providers belong in customer-managed declarative tooling.

The commands are idempotent, read secrets from environment variables, and emit
JSON.
"""

from .cli import build_parser, main

__all__ = ["build_parser", "main"]
