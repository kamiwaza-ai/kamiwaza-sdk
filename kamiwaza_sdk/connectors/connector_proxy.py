"""Schemas for the connector raw-execute proxy (core-mediated egress).

A subscribed connector builds a request against its data source and
hands it to core to execute (the P1 / core-proxied posture): core authorizes the
call, attaches the access token, enforces the connector's egress allowlist, calls
the SaaS, and returns the response. The connector never holds a token and never
egresses to the SaaS directly.

The acting-subject (``subject_token``), ``scope_subset`` and ``lease_duration``
fields are inherited from the mint request -- the same verified-subject trust
boundary applies; the credential core mints to make the call is bound and scoped
identically. See contracts/proxy-execute.md.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .connector_token import ConnectorMintRequest


class ConnectorProxyRequest(ConnectorMintRequest):
    """A connector's raw request for core to execute on its behalf.

    ``Authorization`` is injected by core and never accepted from the caller.
    """

    method: Literal["GET", "POST"] = Field(
        "GET", description="HTTP method (v1: GET or POST)"
    )
    url: str = Field(
        ...,
        description="Absolute URL; its host must be in the connector's egress allowlist",
    )
    params: dict[str, Any] | None = Field(None, description="Query parameters")
    body: dict[str, Any] | None = Field(None, description="JSON body (POST)")
    response_format: Literal["json", "binary"] = Field(
        "json",
        description="How core returns the upstream body: parsed JSON (default), or "
        "base64-wrapped bytes for binary content such as file downloads",
    )


class ConnectorIdentityProxyRequest(BaseModel):
    """A connector's pre-connection identity fetch for core to execute.

    Used only by the ``/v1/whoami`` OAuth-callback path. Unlike
    ``ConnectorProxyRequest`` there is no connection or acting subject yet, so core
    can't mint a scoped credential from storage -- the connector supplies the
    freshly-minted provider ``access_token`` (which core just handed it) and core
    performs the egress the connector pod cannot, enforcing the same workload
    binding + egress allowlist. The token never leaves the deployment mesh and is
    kept out of logs/reprs.
    """

    method: Literal["GET", "POST"] = Field("GET", description="HTTP method")
    url: str = Field(
        ...,
        description="Absolute URL; its host must be in the connector's egress allowlist",
    )
    params: dict[str, Any] | None = Field(None, description="Query parameters")
    body: dict[str, Any] | None = Field(None, description="JSON body (POST)")
    # Identity endpoints are JSON; binary downloads never go through this path.
    response_format: Literal["json"] = Field("json", description="JSON-only")
    access_token: str = Field(
        ...,
        repr=False,
        description="Freshly-minted provider token; core attaches it as the bearer",
    )


class ConnectorProxyResponse(BaseModel):
    """The upstream response, returned to the connector."""

    # extra="allow": retain fields a newer core adds (forward-compat), matching
    # the SDK's other response schemas.
    model_config = ConfigDict(from_attributes=True, extra="allow")

    status_code: int = Field(..., description="Upstream HTTP status code")
    body: Any = Field(None, description="Parsed JSON response body (v1 is JSON-only)")
