"""Kamiwaza client class — skeleton for T5.1.

T5.2 fills in the actual httpx wiring (transport, retry middleware, auth
header injection, default timeout). T5.1 establishes the construction
shape: explicit (base_url, token) constructor + `from_env()` classmethod
factory matching design §4.2.11.

Federation, jobs, retrieval, etc. modules attach as attributes in
subsequent tickets (T5.3, T5.9, T5.36, …). The skeleton intentionally
exposes none of those yet — each landing PR adds its own attribute and
covers it with module-specific tests.
"""

from __future__ import annotations

import os
from typing import Optional


class Kamiwaza:
    """Client handle for the Kamiwaza platform.

    Args:
        base_url: Cluster URL, e.g. ``https://kamiwaza.test``.
        token: Personal Access Token (PAT) for authentication.

    Example:
        >>> client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-...")
        >>> # T5.3+ adds client.federations, client.jobs, etc.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.token = token

    @classmethod
    def from_env(
        cls,
        base_url_env: str = "KAMIWAZA_BASE_URL",
        token_env: str = "KAMIWAZA_TOKEN",
    ) -> "Kamiwaza":
        """Construct a client from env vars (canonical entry point).

        T5.1 ships the basic env-var resolution. T5.2 expands with retry
        middleware setup + httpx client creation. Raises ``KamiwazaError``
        when the required env vars are absent (operator-readable).

        Args:
            base_url_env: Env var name holding the cluster base URL.
            token_env: Env var name holding the PAT.

        Returns:
            Configured Kamiwaza client instance.
        """
        from kamiwaza.exceptions import KamiwazaError

        base_url: Optional[str] = os.environ.get(base_url_env)
        token: Optional[str] = os.environ.get(token_env)
        if not base_url:
            raise KamiwazaError(
                f"{base_url_env} env var is not set; cannot construct Kamiwaza "
                "client via from_env(). Set it to your cluster URL "
                "(e.g. https://kamiwaza.example.com) or use the explicit "
                "Kamiwaza(base_url=..., token=...) constructor."
            )
        if not token:
            raise KamiwazaError(
                f"{token_env} env var is not set; cannot construct Kamiwaza "
                "client via from_env(). Set it to your Personal Access "
                "Token or use the explicit Kamiwaza(base_url=..., token=...) "
                "constructor."
            )
        return cls(base_url=base_url, token=token)
