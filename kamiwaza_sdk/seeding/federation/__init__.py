"""Federation / shared_idp seeding + ReBAC access CLI.

A deterministic, operator-facing utility (``kamiwaza-fed``) that productizes the
shared_idp stand-up + resource-access management that previously lived only in
test fixtures (``tests/integration/_mini_clearance.py``):

* ``access``  — manage ReBAC grants on resources (subjects.grants)
* ``fed``     — shared_idp federation lifecycle (pair / status / allow-user)
* ``dataset`` / ``gate`` / ``attr`` — the gated-retrieval setup
* ``idp``     — (dev) Keycloak-side shared-realm seeding (realm / client / mapper /
                personas), the one half that needs Keycloak admin rather than the
                platform API

Design invariants (shared with ``kamiwaza-seed``): idempotent, secrets from env
never argv, thin wrappers around SDK / Keycloak-admin methods, JSON output.
"""

from .cli import build_parser, main

__all__ = ["build_parser", "main"]
