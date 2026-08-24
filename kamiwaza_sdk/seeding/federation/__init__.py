"""Federation / shared_idp seeding + ReBAC access CLI.

A deterministic, operator-facing utility (``kamiwaza-federation``) that productizes the
shared_idp stand-up + resource-access management that previously lived only in
test fixtures (``tests/integration/_mini_clearance.py``):

* ``access``  — manage ReBAC grants on resources (subjects.grants)
* ``fed``     — shared_idp federation lifecycle (pair / status / allow-user)
* ``dataset`` / ``gate`` / ``attr`` — the gated-retrieval setup
* ``idp``     — (DEV/TEST ONLY) Keycloak-side shared-realm seeding (realm / client /
                mapper / personas), the one half that needs Keycloak admin rather
                than the platform API

``idp bootstrap`` and ``idp persona`` call the Keycloak ADMIN REST API
(``/admin/*``), which the platform ingress deliberately does NOT expose — so they
require direct Keycloak access (e.g. ``kubectl port-forward svc/keycloak 8080:80``
then ``--kc-url http://localhost:8080``). ``idp token`` only does a public ROPC
grant and works against the normal ingress URL. For PRODUCTION, the shared realm /
client / clearance-mapper / user-profile policy should be provisioned
declaratively by the auth chart's install-time Keycloak init-Job pipeline (the
same path that seeds the ``kamiwaza`` realm), not by this command — see the
ENG-8571 follow-up. This CLI remains the fast path for dev/test stand-up.

Design invariants (shared with ``kamiwaza-seed``): idempotent, secrets from env
never argv, thin wrappers around SDK / Keycloak-admin methods, JSON output.
"""

from .cli import build_parser, main

__all__ = ["build_parser", "main"]
