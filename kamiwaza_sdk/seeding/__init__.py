# kamiwaza_sdk/seeding/

"""Generic, parameterized seeding helpers + CLI for the Kamiwaza platform.

This package is deliberately profile-free: it exposes deterministic operations
(create a workroom, register an external model, install an extension by name,
create an agent/conversation) that take explicit arguments. Environment-specific
data (which models, which extensions, which workroom) belongs to the caller —
e.g. the nightly UAT seeding profile — not here.
"""

from .client import build_client_from_env, scoped_client_for_workroom

__all__ = ["build_client_from_env", "scoped_client_for_workroom"]
