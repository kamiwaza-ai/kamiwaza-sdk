"""Tests for kamiwaza_extensions_lib.local_dev (ENG-4318).

Covers test scenarios TS-1..TS-4 (prepare_bridge_context error/happy paths)
and TS-9..TS-13 (URL helpers).
"""

from __future__ import annotations

import base64
import json
import socket
import time
from pathlib import Path

import pytest

from kamiwaza_extensions.connections import ConnectionManager
from kamiwaza_extensions_lib.local_dev import (
    BridgeContext,
    LocalDevAuthError,
    extract_extra_hosts,
    is_loopback_url,
    prepare_bridge_context,
    rewrite_bare_loopback_url,
)
from kamiwaza_sdk.token_store import StoredToken


def _make_jwt(*, exp: int | None = None, sub: str | None = "user-1") -> str:
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).decode().rstrip("=")
    claims: dict = {}
    if exp is not None:
        claims["exp"] = exp
    if sub is not None:
        claims["sub"] = sub
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def _make_manager(tmp_path: Path) -> ConnectionManager:
    return ConnectionManager(config_dir=tmp_path / ".kamiwaza")


# ---------------------------------------------------------------------------
# is_loopback_url — TS-12, TS-13
# ---------------------------------------------------------------------------


def _ok_resolver(host):
    """Test resolver — pretends every non-loopback host is resolvable."""
    return "1.2.3.4"


def _nxdomain_resolver(host):
    raise OSError(f"resolver mock NXDOMAIN: {host}")


@pytest.mark.unit
class TestIsLoopbackUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000",
            "http://127.0.0.1:8080",
            "http://[::1]:8000",
            "https://kamiwaza.test",
            "https://dev.local",
            "https://api.dev.local/api",
        ],
    )
    def test_loopback_hostnames_detected(self, url):
        # TS-12 — bare loopbacks + reserved TLDs without needing DNS
        assert is_loopback_url(url, resolver=_ok_resolver) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.kamiwaza.ai",
            "https://kamiwaza.example.com",
            "https://1.2.3.4/api",
        ],
    )
    def test_non_loopback_hostnames(self, url):
        # TS-13 — resolves via injected resolver, returns False
        assert is_loopback_url(url, resolver=_ok_resolver) is False

    def test_unresolvable_non_reserved_hostname_treated_as_loopback(self):
        # If the developer has the hostname in /etc/hosts only (not DNS),
        # containers can't reach it — flag it as loopback so extra_hosts
        # gets injected.
        assert is_loopback_url(
            "https://my-internal-host", resolver=_nxdomain_resolver
        ) is True

    def test_default_resolver_times_out_on_slow_dns(self, monkeypatch):
        """PR #87 round-3 fix — DNS lookup must be capped via a real
        wall-clock timeout (ThreadPoolExecutor.future.result), not
        socket.setdefaulttimeout (which is a no-op for getaddrinfo).
        Round-11 (codex GH High): patch target now ``getaddrinfo`` —
        round-11 switched the resolver from ``gethostbyname`` (IPv4
        only) to ``getaddrinfo`` (dual-stack) so AAAA-only Kamiwaza
        deployments resolve correctly.
        """
        import time as _time
        from kamiwaza_extensions_lib import local_dev as ld

        # Replace the underlying resolver with one that hangs longer
        # than the configured cap.
        def slow_resolver(host, *args, **kwargs):
            _time.sleep(10.0)  # would block for 10s without the cap
            return [(0, 0, 0, "", ("1.2.3.4", 0))]

        monkeypatch.setattr("socket.getaddrinfo", slow_resolver)
        # Tighten the cap so the test runs fast.
        monkeypatch.setattr(ld, "_DNS_TIMEOUT_S", 0.2)

        start = _time.monotonic()
        with pytest.raises(OSError, match="exceeded"):
            ld._default_resolver("slow.example.com")
        elapsed = _time.monotonic() - start
        # The cap is 0.2s; allow generous slack for thread scheduling
        # but the test would block 10s if the timeout were a no-op.
        assert elapsed < 2.0, (
            f"DNS timeout cap was not honored — elapsed {elapsed:.2f}s"
        )

    def test_default_resolver_resolves_aaaa_only_host(self, monkeypatch):
        """PR #87 round-11 review (codex GH High) — resolver must
        succeed for hosts that publish only IPv6 (AAAA) records.

        Bug: prior implementation used ``socket.gethostbyname`` which
        is IPv4-only. An AAAA-only Kamiwaza hostname raised
        ``gaierror`` here, ``is_loopback_url`` then treated it as
        "unresolvable from host", and ``build_compose_extra_hosts``
        silently routed platform traffic to the developer's machine
        via ``host-gateway`` — the developer's auth would fail and
        their requests would loop back to localhost.
        """
        from kamiwaza_extensions_lib import local_dev as ld

        # Stub: a host that only has an AAAA record. ``getaddrinfo``
        # returns the v6 sockaddr 4-tuple ``(addr, port, flowinfo, scope)``.
        def v6_only(host, *args, **kwargs):
            return [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("2001:db8::1", 0, 0, 0),
                ),
            ]

        monkeypatch.setattr("socket.getaddrinfo", v6_only)

        # Resolver returns the v6 address as a string.
        addr = ld._default_resolver("ipv6-only.kamiwaza.example.com")
        assert addr == "2001:db8::1"

    def test_handles_malformed_url(self):
        assert is_loopback_url("not-a-url", resolver=_ok_resolver) is False
        assert is_loopback_url("", resolver=_ok_resolver) is False


# ---------------------------------------------------------------------------
# rewrite_bare_loopback_url — covers AC-4 hostname preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRewriteBareLoopbackUrl:
    def test_rewrites_localhost(self):
        assert (
            rewrite_bare_loopback_url("http://localhost:8000")
            == "http://host.docker.internal:8000"
        )

    def test_rewrites_127_0_0_1(self):
        assert (
            rewrite_bare_loopback_url("http://127.0.0.1:8000/api")
            == "http://host.docker.internal:8000/api"
        )

    def test_rewrites_ipv6_loopback(self):
        assert (
            rewrite_bare_loopback_url("http://[::1]:8000")
            == "http://host.docker.internal:8000"
        )

    def test_preserves_named_loopback_hostnames(self):
        # Named hostnames have TLS cert bindings; preserve them.
        assert rewrite_bare_loopback_url("https://kamiwaza.test") == "https://kamiwaza.test"
        assert rewrite_bare_loopback_url("https://dev.local") == "https://dev.local"

    def test_preserves_non_loopback(self):
        assert (
            rewrite_bare_loopback_url("https://api.kamiwaza.ai")
            == "https://api.kamiwaza.ai"
        )

    def test_preserves_path_and_query(self):
        assert (
            rewrite_bare_loopback_url("http://localhost:8000/api?foo=bar")
            == "http://host.docker.internal:8000/api?foo=bar"
        )


# ---------------------------------------------------------------------------
# extract_extra_hosts — TS-9, TS-10, TS-11
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractExtraHosts:
    def test_named_loopback_returns_host_gateway_entry(self):
        # TS-9
        assert extract_extra_hosts(
            "https://kamiwaza.test", resolver=_ok_resolver
        ) == ["kamiwaza.test:host-gateway"]

    def test_named_loopback_with_port(self):
        # Port should not appear in extra_hosts entry
        assert extract_extra_hosts(
            "https://kamiwaza.test:8443", resolver=_ok_resolver
        ) == ["kamiwaza.test:host-gateway"]

    def test_non_loopback_returns_empty(self):
        # TS-10
        assert extract_extra_hosts(
            "https://api.kamiwaza.ai", resolver=_ok_resolver
        ) == []

    def test_bare_loopback_returns_empty(self):
        # TS-11 — URL rewrite covers bare loopbacks; no extra_hosts needed
        assert extract_extra_hosts(
            "http://localhost:8000", resolver=_ok_resolver
        ) == []
        assert extract_extra_hosts(
            "http://127.0.0.1:8000", resolver=_ok_resolver
        ) == []
        assert extract_extra_hosts(
            "http://[::1]:8000", resolver=_ok_resolver
        ) == []

    def test_handles_malformed_url(self):
        assert extract_extra_hosts("", resolver=_ok_resolver) == []
        assert extract_extra_hosts("not-a-url", resolver=_ok_resolver) == []

    def test_unresolvable_hostname_gets_extra_hosts(self):
        # A hostname only in /etc/hosts on the developer's machine — the
        # container needs extra_hosts to resolve it.
        assert extract_extra_hosts(
            "https://my-internal", resolver=_nxdomain_resolver
        ) == ["my-internal:host-gateway"]


# ---------------------------------------------------------------------------
# prepare_bridge_context — TS-1, TS-2, TS-3, TS-4
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPrepareBridgeContext:
    def test_returns_context_with_valid_connection_and_bearer(self, tmp_path):
        # TS-1
        mgr = _make_manager(tmp_path)
        future_exp = int(time.time()) + 3600
        token = StoredToken(
            access_token=_make_jwt(exp=future_exp, sub="user-42"),
            refresh_token=None,
            expires_at=float(future_exp),
        )
        mgr.add_connection("default", "https://kamiwaza.test/api", token)

        ctx = prepare_bridge_context(connection_manager=mgr)

        assert isinstance(ctx, BridgeContext)
        assert ctx.bearer_token == token.access_token
        assert ctx.expires_at == future_exp
        assert ctx.user_id == "user-42"

    def test_raises_when_no_active_connection(self, tmp_path):
        # TS-2
        mgr = _make_manager(tmp_path)
        with pytest.raises(LocalDevAuthError, match="run.*kz-ext login"):
            prepare_bridge_context(connection_manager=mgr)

    def test_raises_when_active_connection_has_no_bearer(self, tmp_path):
        # TS-3
        mgr = _make_manager(tmp_path)
        # Add a connection but with empty access token
        future_exp = time.time() + 3600
        token = StoredToken(
            access_token="",
            refresh_token=None,
            expires_at=future_exp,
        )
        mgr.add_connection("default", "https://kamiwaza.test/api", token)

        with pytest.raises(LocalDevAuthError, match="no stored bearer"):
            prepare_bridge_context(connection_manager=mgr)

    def test_raises_when_jwt_exp_in_past(self, tmp_path):
        # TS-4
        mgr = _make_manager(tmp_path)
        past_exp = int(time.time()) - 60  # 60s ago
        token = StoredToken(
            access_token=_make_jwt(exp=past_exp),
            refresh_token=None,
            expires_at=float(past_exp),
        )
        mgr.add_connection("default", "https://kamiwaza.test/api", token)

        with pytest.raises(LocalDevAuthError, match="expired"):
            prepare_bridge_context(connection_manager=mgr)

    def test_accepts_token_without_exp_claim(self, tmp_path):
        """A bearer with no exp claim is still usable — fail-loud is only
        for definitively-expired tokens, not unparseable ones (so long as
        the JWT has a usable sub claim)."""
        mgr = _make_manager(tmp_path)
        far_future = time.time() + 86400
        token = StoredToken(
            access_token=_make_jwt(exp=None, sub="user-1"),
            refresh_token=None,
            expires_at=far_future,
        )
        mgr.add_connection("default", "https://kamiwaza.test/api", token)

        ctx = prepare_bridge_context(connection_manager=mgr)
        assert ctx.expires_at is None  # no exp claim in JWT
        assert ctx.user_id == "user-1"

    def test_raises_when_jwt_has_no_sub_claim(self, tmp_path):
        """PR #87 review (Critical #2) — opaque PATs / API keys decode to
        empty claims and previously produced a silent no-op auth path
        (TS middleware bails when sub is missing). Now fail-loud upstream."""
        mgr = _make_manager(tmp_path)
        future_exp = int(time.time()) + 3600
        # JWT with no sub claim — simulates an opaque PAT or non-JWT bearer
        token = StoredToken(
            access_token=_make_jwt(exp=future_exp, sub=None),
            refresh_token=None,
            expires_at=float(future_exp),
        )
        mgr.add_connection("default", "https://kamiwaza.test/api", token)

        with pytest.raises(LocalDevAuthError, match="not a JWT"):
            prepare_bridge_context(connection_manager=mgr)

    def test_raises_for_opaque_non_jwt_bearer(self, tmp_path):
        """A bearer that's not a JWT at all (e.g. a raw PAT string from
        kz-ext login --api-key) should fail-loud, not silently no-op."""
        mgr = _make_manager(tmp_path)
        future_exp = time.time() + 3600
        token = StoredToken(
            access_token="kz_pat_random_opaque_string_not_a_jwt",
            refresh_token=None,
            expires_at=future_exp,
        )
        mgr.add_connection("default", "https://kamiwaza.test/api", token)

        with pytest.raises(LocalDevAuthError, match="not a JWT"):
            prepare_bridge_context(connection_manager=mgr)
