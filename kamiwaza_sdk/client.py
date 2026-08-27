# kamiwaza_sdk/client.py

from collections import OrderedDict
import logging
import os
import random
import time
from typing import Any, Optional

import requests  # type: ignore[import-untyped]

from .exceptions import (
    APIError,
    AuthenticationError,
    FederationPairTimeoutError,
    NonAPIResponseError,
    VectorDBUnavailableError,
)
from .services.models import ModelService
from .services.serving import ServingService
from .services.catalog import CatalogService
from .services.prompts import PromptsService
from .services.embedding import EmbeddingService
from .services.activity import ActivityService
from .services.lab import LabService
from .services.auth import AuthService
from .services.authz import AuthzService
from .authentication import Authenticator, ApiKeyAuthenticator
from .services.ingestion import IngestionService
from .services.openai import OpenAIService
from .services.apps import AppService
from .services.tools import ToolService
from .services.context import ContextService
from .services.skills import SkillsService
from .services.enclaves import EnclavesService
from .services.workrooms import WorkroomService

logger = logging.getLogger(__name__)

_AUTH_ERROR_DETAIL_MAX_LEN = 500
_AUTH_ERROR_DETAIL_TRUNCATED_SUFFIX = "... [truncated]"
_VERIFY_SSL_FALSE_VALUES = {"false", "0", "no"}

# Gray Matter briefly returns this exact Envoy response while dynamic runtime
# routes converge (ENG-8430). Retry read-only calls only: a write that receives
# it has an ambiguous effect and must remain the caller's decision to replay.
_GREYMATTER_ROUTE_RETRY_SCHEDULE_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0)
_GREYMATTER_NO_HEALTHY_UPSTREAM = "no healthy upstream"
_READ_ONLY_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


# ----------------------------------------------------------------------------
# psk_propagation_timeout retry middleware (T7.4 / ENG-5038)
#
# Per design §4.2.1, the federation pair flow may return HTTP 503 with body
# ``{"detail": {"reason": "psk_propagation_timeout"}}`` while DataHub is
# still racing to make the freshly-stored PSK available to the receiver.
# The SDK retries on the exponential schedule below, capped by a 90s
# wall-clock budget so a stuck cluster cannot hang the SDK indefinitely.
# On exhaustion, surfaces as FederationPairTimeoutError (typed) so customer
# code can branch on the specific terminal failure.
#
# Ported from kamiwaza/client.py (httpx-based) to requests.Session
# semantics per OQ-15 (transport stack stays on requests for v1.x; httpx
# modernization deferred to v1.1+).
# ----------------------------------------------------------------------------

_RETRY_BACKOFF_SCHEDULE_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
"""Upper-bound exponential backoff schedule. The wall-clock cap trims it.

The schedule sums to 127s but the deadline check in ``_request`` short-
circuits before the 64s entry would push total elapsed past 90s, so the
effective tail is usually never slept on. Schedule kept deterministic so
behavior is testable without a clock-injection layer."""

_RETRY_WALL_CLOCK_BUDGET_SECONDS = 90.0
"""Hard cap on total wall-clock time spent in psk_propagation_timeout retry."""

_PSK_PROPAGATION_TIMEOUT_REASON = "psk_propagation_timeout"
_WORKROOM_SCOPE_HEADER = "X-Workroom-Id"


def _is_psk_propagation_timeout(response: Any) -> bool:
    """Match the design §4.2.1 retry-eligible 503 shape exactly.

    Server returns ``HTTPException(status_code=503, detail={"reason":
    "psk_propagation_timeout", "elapsed_seconds": ..., "remediation": ...})``.
    Status code is part of the match so a pathological non-503 with the
    matching reason string doesn't trigger retry.
    """
    if response.status_code != 503:
        return False
    try:
        body = response.json()
    except (ValueError, AttributeError):
        return False
    if not isinstance(body, dict):
        return False
    detail = body.get("detail")
    if not isinstance(detail, dict):
        return False
    return detail.get("reason") == _PSK_PROPAGATION_TIMEOUT_REASON


def _is_greymatter_route_transient(response: Any, method: str) -> bool:
    """Match the exact read-only Envoy response seen during route convergence."""
    if method.upper() not in _READ_ONLY_HTTP_METHODS:
        return False
    if response.status_code != 503:
        return False
    response_text = getattr(response, "text", "")
    return response_text.strip().lower() == _GREYMATTER_NO_HEALTHY_UPSTREAM


_RETRYABLE_503_WALL_CLOCK_BUDGET_SECONDS = 60.0
"""Hard cap on total wall-clock time spent honoring server Retry-After hints."""

_RETRYABLE_503_MAX_SLEEP_SECONDS = 30.0
"""Per-attempt ceiling on the server's hint."""

_RETRYABLE_503_MIN_SLEEP_SECONDS = 1.0
"""Floor under the server's hint.

The delay is server-controlled input. Without a floor, a hint of 0.0001 turns
every SDK caller into an amplifier against the customer's own cluster --
measured at ~1712 req/s, ~10^5 attempts inside the wall-clock budget. Since
``KAMIWAZA_VERIFY_SSL=false`` is a supported mode, an intercepting proxy can
inject that hint into any 503, so this is a hostile-input boundary and not
merely a sanity check.
"""

_RETRYABLE_503_MAX_ATTEMPTS = 6
"""Hard cap on retries, independent of the wall-clock budget.

The budget alone bounds total *time*, not attempt *count*: a small hint spends
it in thousands of requests. The cap binds for hints up to roughly 9s; above
that the budget binds first once jitter is counted, so the two are not
interchangeable and neither alone is sufficient.
"""

_RETRYABLE_503_JITTER_FRACTION = 0.1
"""Spread of the random jitter added to each delay, as a fraction of it.

Every client fenced by the same operation receives the same hint and would
otherwise retry in lockstep, re-contending as a thundering herd.
"""


def _retry_jitter_unit() -> float:
    """Return a jitter multiplier in ``[0, 1)``.

    A seam, not indirection for its own sake: tests monkeypatch this to 0.0 so
    schedules stay exactly assertable while production keeps real jitter.
    """
    return random.random()


_RETRYABLE_503_CODES = frozenset(
    {
        # Authority fence briefly held by another operation; the workroom is
        # untouched in this state. (ENG-10506)
        "workroom_authority_unavailable",
        # NOT "the request never ran" -- it did. auto_provisioner._provision()
        # reaps stranded seed rows and calls serving_service.deploy_model()
        # *before* raising this on the on_demand path, and
        # _handle_provision_failure raises it after a deploy attempt too. What
        # makes the replay safe is that provisioning is gated and convergent:
        # the provisioner holds _PROVISIONER_LOCK and consults _LAST_ATTEMPT,
        # so a re-issued call inside the warmup window observes the in-flight
        # attempt instead of starting a second one, and the freshly created
        # row is non-terminal so the reaper will not take it.
        #
        # Caveat worth knowing: those gates are module-level process-local
        # globals, so the window is shared within a core replica, not across
        # them. A retry landing on a replica whose _LAST_ATTEMPT is unset can
        # re-enter _provision(); the deploy pre-check is then the only thing
        # standing between that and a duplicate deployment. Not demonstrated,
        # but it is where this admission's safety actually rests. (ENG-10527)
        "embedding_deploying",
        # Transport failure reaching the embedding runtime. Raised from
        # httpx.RequestError, which includes read timeouts, so the backend may
        # in principle have seen the request. Every route that can raise it is
        # a pure computation with no persistent effect, so a replay is
        # harmless either way. The full set -- this is the standing rule when
        # core adds a raise site, so keep it complete:
        #   services/embedding/api.py  create_embedding, get_embedding,
        #                              chunk_text, embed_chunks
        #   services/embedding/api.py  _translate_upstream_runtime_error,
        #     used by all four of the above. Note this one emits on
        #     httpx.HTTPStatusError -- the upstream itself returned 503 --
        #     not on a transport failure.
        #   services/embedding/exceptions.py  translate_to_http_503, reached
        #     via context/lib/embedding_availability.py from:
        #       context/api/search.py:155 (_search_http_error) -> /search,
        #         /search/unified, /search/simple, /retrieve, /search/retrieve
        #       context/api/search.py:388 -> POST /search/agentic
        #       context/api/dataset_indexes.py:463 (_run_unified_search) ->
        #         POST /context/dataset-indexes/{binding_id}/search
        # The agentic search path is pure but not cheap: a read-timeout replay
        # re-runs a multi-iteration LLM workload.
        "embedding_runtime_unreachable",
        # Pre-flight discovery failure on an idempotent PUT
        # (context/api/embedding_model.py). Core emits this code a second way,
        # from ontology.py inside the PUT /ontologies/{id}/model-bindings
        # write, as a raw HTTPException whose payload is nested under
        # "detail" with no retry_after_seconds mirror -- so it does not match
        # below and is not retried. That is load-bearing, not incidental: the
        # ontology site is a *mutating* write whose replay safety nobody has
        # reviewed. See the note on the mirror in _retry_after_seconds.
        "discovery_unavailable",
        # Raised during pin validation. No caller-visible effect, and the
        # internal seeds are convergent on replay: resolve() gates its rebind
        # write on `allow_persist and discovery_ok`, and discovery_ok is False
        # on this path, so no pin is committed; seed_binding_from_workroom_attrs
        # returns without writing when a row exists and rolls back on
        # IntegrityError.
        "pinned_discovery_unavailable",
        # Two raise sites, and only one of them means "nothing ran":
        #   router.py:658 (GlobalOntologyProvisioningPendingError) comes from
        #     _precheck_ontology_create, which fires before the plan builder --
        #     nothing ran.
        #   router.py:639 (ensure_default_global_instance() returned None) is
        #     reached through _create_ontology_candidate, which calls
        #     app_service.create_deployment() first. A real deployment was
        #     created.
        # Admitted on convergence, as with embedding_deploying: that path's
        # `except Exception` runs _reconcile_context_deployment ->
        # reconcile_cleanup_intent, whose contract is to retain one winner or
        # strictly remove and verify one loser, so it reclaims what it made.
        # The route also needs an AlreadyExists plus every
        # SCOPED_INSTANCE_RECOVERY_ATTEMPTS finding nothing. Worst case is
        # bounded, self-cleaning create/teardown churn across the replays.
        "global_ontology_provisioning",
    }
)
"""Server ``code`` values this client will retry.

Deliberately an allowlist rather than "any 503 carrying a hint" (ENG-10506):
a hint means the server *believes* the condition is transient, which is not
the same as the SDK being safe to silently re-issue the call. Adding a code
here is a decision about idempotency, so it stays explicit.

Every code above was reviewed against its raise site in core (ENG-10516) and
admitted on the same test: the 503 fires because a dependency was unreachable
or not yet ready, so the requested work did not take effect. HTTP verb alone
is not the criterion — a POST whose handler fails pre-flight is replayable,
and a code that could fire *after* a persistent effect would not be, whatever
its verb.
"""


def _retry_after_seconds(response: Any) -> Optional[float]:
    """Return the server's retry delay for a retryable 503, else ``None``.

    ``kamiwaza.lib.http_errors.service_unavailable()`` renders retryable
    503s as the bare ``ServiceUnavailable503Detail`` body
    ``{"code": ..., "message": ..., "retry_after_seconds": N}`` (see
    ``BareDetailHTTPException`` -- the body is the detail itself, not
    ``{"detail": ...}``) and mirrors the value in a ``Retry-After`` header.

    Deliberately keyed on the **bare top-level body**, not the ``Retry-After``
    header, even though core sets both. Core's own docstring calls
    ``retry_after_seconds`` a "mirror of the Retry-After header for clients
    that don't read response headers", so reading only the mirror looks like
    an oversight -- it is not. Emitters that go through
    ``service_unavailable()`` always set the mirror, while the one raise site
    that hand-rolls a raw ``HTTPException`` (ontology.py, behind a *mutating*
    model-bindings PUT) sets only the header and nests its payload under
    ``detail``. Honoring the header would silently switch on automatic replay
    of that write, which nobody has reviewed for replay safety. The mirror is
    the narrower and safer signal; keep it that way until the ontology site is
    normalized and reviewed.

    Both halves must line up: the ``code`` must be one we have decided is
    safe to re-issue (``_RETRYABLE_503_CODES``), and the delay must be
    usable. Status code is part of the match so a stray hint on a 4xx -- a
    terminal conflict, say -- cannot turn a permanent failure into a retry
    loop. Non-numeric and non-positive delays are rejected: they carry no
    usable wait, and treating them as zero would spin.
    """
    if response.status_code != 503:
        return None
    try:
        body = response.json()
    except (ValueError, AttributeError):
        return None
    if not isinstance(body, dict):
        return None
    if body.get("code") not in _RETRYABLE_503_CODES:
        return None
    value = body.get("retry_after_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return float(value)


def _contains_consumable_stream(value: Any) -> bool:
    """True when ``value`` holds something a retry cannot re-read.

    Walks the containers ``requests`` accepts for ``data`` / ``files`` looking
    for file-like objects and one-shot iterators. Strings and bytes are
    explicitly repeatable; dicts/lists are inspected element-wise because
    ``files={"f": ("name", fh)}`` hides the handle one level down.
    """
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return False
    if hasattr(value, "read"):
        return True
    if isinstance(value, dict):
        return any(_contains_consumable_stream(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_consumable_stream(v) for v in value)
    return hasattr(value, "__iter__")


def _request_body_is_replayable(kwargs: dict) -> bool:
    """True when the outgoing body can safely be sent a second time.

    ``requests`` consumes a streamed body to EOF on the first attempt, so
    replaying the same kwargs would re-encode it from its current position --
    silently uploading an empty or truncated file rather than failing. A 503
    surfaced to the caller is far better than corrupt data written on their
    behalf, so a non-replayable body opts out of retry entirely.
    """
    return not any(
        _contains_consumable_stream(kwargs.get(key)) for key in ("data", "files")
    )


def _psk_timeout_error_from_response(response: Any) -> FederationPairTimeoutError:
    """Construct a typed FederationPairTimeoutError from the final retried
    response when the wall-clock budget is exhausted. Carries the structured
    body so customer code can inspect ``elapsed_seconds`` / ``remediation``."""
    try:
        body = response.json()
    except (ValueError, AttributeError):
        body = None
    return FederationPairTimeoutError(
        "Federation pair barrier timed out: psk_propagation_timeout persisted "
        "past the 90s SDK retry budget.",
        status_code=response.status_code,
        body=body,
    )


def _truncate_with_suffix(value: str, max_len: int = _AUTH_ERROR_DETAIL_MAX_LEN) -> str:
    """Truncate ``value`` to ``max_len`` chars, appending a suffix when cut.

    A naked slice is ambiguous — a 500-char return is indistinguishable from
    a legitimately short body that happens to fit. The suffix makes the
    truncation explicit to anyone reading logs or exception messages.
    """
    if len(value) <= max_len:
        return value
    return value[:max_len] + _AUTH_ERROR_DETAIL_TRUNCATED_SUFFIX


def _extract_server_detail(response, max_len: int = _AUTH_ERROR_DETAIL_MAX_LEN) -> str:
    """Extract a short, embeddable description of a server error response.

    Prefers the JSON ``detail`` field (FastAPI convention) so the caller sees
    the server's actual message. Falls back to the serialized JSON body, then
    raw text. Output is always truncated to ``max_len`` characters (with an
    explicit ``... [truncated]`` suffix when cut) to prevent multi-KB
    proxy/gateway HTML error pages from bloating log lines and exception
    strings.
    """
    try:
        body = response.json()
    except (ValueError, AttributeError):
        return _truncate_with_suffix(response.text or "", max_len)

    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        if isinstance(detail, str):
            return _truncate_with_suffix(detail, max_len)
        return _truncate_with_suffix(str(detail), max_len)
    return _truncate_with_suffix(str(body), max_len)


def _verify_ssl_disabled_from_env() -> bool:
    value = os.environ.get("KAMIWAZA_VERIFY_SSL")
    if value is None:
        return False
    return value.strip().lower() in _VERIFY_SSL_FALSE_VALUES


class _RetryState:
    """Per-request retry bookkeeping.

    Bundled into one object so the retry helpers stay inside the 4-argument
    cap and ``_request`` keeps a single local instead of four counters.
    """

    def __init__(self, method: str, path: str) -> None:
        now = time.monotonic()
        self.method = method
        self.path = path
        # Fixed local schedule for one known reason.
        self.psk_deadline = now + _RETRY_WALL_CLOCK_BUDGET_SECONDS
        self.psk_idx = 0
        # Server-directed: follows whatever delay the server asks for.
        self.deadline_503 = now + _RETRYABLE_503_WALL_CLOCK_BUDGET_SECONDS
        self.attempts_503 = 0
        # Client-directed: exact Gray Matter/Envoy route-convergence response.
        self.greymatter_route_idx = 0


class KamiwazaClient:
    _RECENT_DATASET_TTL_SECONDS = 30.0
    _RECENT_DATASET_MAX = 1024

    # Retry window for PUT-after-create/update schema operations.
    # Total sleep time sums to 5.0s.
    _DATASET_SCHEMA_PUT_RETRY_DELAYS_SECONDS = (0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0, 0.5)

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        authenticator: Optional[Authenticator] = None,
        log_level: int = logging.INFO,
        *,
        verify: Optional[Any] = None,
        ca_bundle: Optional[str] = None,
        owns_authenticator: bool = False,
    ):
        """Construct a KamiwazaClient.

        Args:
            base_url: Cluster API root (e.g. ``"https://kamiwaza.test/api"``).
                Falls back to ``KAMIWAZA_BASE_URL`` then ``KAMIWAZA_BASE_URI``
                env vars when not supplied.
            api_key: Optional PAT. Falls back to ``KAMIWAZA_API_KEY`` then
                ``KAMIWAZA_API_TOKEN`` env vars.
            authenticator: Optional ``Authenticator`` instance; takes
                precedence over ``api_key`` when supplied.
            owns_authenticator: Close a supplied authenticator with this client.
                Defaults to ``False`` because caller-supplied authenticators may
                be shared by more than one client.
            log_level: Python logging level (default INFO).
            verify: TLS verification setting. ``True`` (default — system
                bundle), ``False`` (disable; warns), or a path string
                (custom CA bundle).
            ca_bundle: Path to a custom CA bundle (PEM). Sugar for
                ``verify=<path>`` — clearer name when callers know they're
                pointing at a specific file. ``ca_bundle`` wins over
                ``verify`` when both supplied.

        TLS verification precedence (T7.13 / ENG-5047, closes ENG-5015):

            explicit ``ca_bundle=`` >
            explicit ``verify=`` >
            ``KAMIWAZA_VERIFY_SSL`` env var (existing behavior) >
            ``REQUESTS_CA_BUNDLE`` env var (honored by requests natively) >
            default ``True`` (system bundle)

        For self-signed cluster certs, fetch the cluster's CA via:

            kubectl get secret kamiwaza-ca-root-ca -n kamiwaza \\
                -o jsonpath='{.data.tls\\.crt}' | base64 -d > cluster-ca.pem

        then construct: ``KamiwazaClient(ca_bundle="cluster-ca.pem")``.
        """
        # Configure logging
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        self.logger = logger

        resolved_base_url = (
            base_url
            or os.environ.get("KAMIWAZA_BASE_URL")
            or os.environ.get("KAMIWAZA_BASE_URI")
        )
        if not resolved_base_url:
            raise ValueError(
                "base_url is required. Provide it directly or set KAMIWAZA_BASE_URL or KAMIWAZA_BASE_URI."
            )

        self.base_url = resolved_base_url.rstrip("/")
        self.session = requests.Session()
        self._recent_datasets: "OrderedDict[str, float]" = OrderedDict()
        self._default_headers: dict[str, str] = {}

        # TLS verification: explicit kwargs > env vars > default True.
        # ca_bundle is sugar for verify=<path>; wins over verify when both.
        if ca_bundle is not None:
            self.session.verify = ca_bundle
        elif verify is not None:
            self.session.verify = verify
            if verify is False:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                self.logger.info("SSL verification disabled (verify=False kwarg)")
        elif _verify_ssl_disabled_from_env():
            self.session.verify = False
            # Suppress SSL warnings when verification is disabled
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self.logger.info("SSL verification disabled (KAMIWAZA_VERIFY_SSL=false)")

        # Initialize _auth_service directly
        self._auth_service = AuthService(self)

        self.authenticator: Optional[Authenticator] = None
        self._owned_authenticator: Optional[Authenticator] = None
        if authenticator is not None:
            self.authenticator = authenticator
        else:
            api_key = (
                api_key
                or os.environ.get("KAMIWAZA_API_KEY")
                or os.environ.get("KAMIWAZA_API_TOKEN")
            )
            self.authenticator = ApiKeyAuthenticator(api_key) if api_key else None
        if authenticator is not None and owns_authenticator:
            self._owned_authenticator = authenticator
        self._owns_authenticator = self._owned_authenticator is not None

        # Don't authenticate during initialization - let it happen on first request

    def close(self) -> None:
        """Release the authentication and platform HTTP transports.

        Idempotent — repeated close() calls are safe (Session.close()
        does its own idempotency).
        """
        close_authenticator = getattr(self._owned_authenticator, "close", None)
        try:
            if self._owns_authenticator and callable(close_authenticator):
                close_authenticator()
        finally:
            self.session.close()

    def __enter__(self) -> "KamiwazaClient":
        return self

    def __exit__(
        self,
        _exc_type: Any,
        _exc: Any,
        _tb: Any,
    ) -> None:
        self.close()

    def _note_recent_dataset_change(self, dataset_urn: str) -> None:
        """Mark a dataset as recently created/updated for eventual-consistency retries."""
        if not isinstance(dataset_urn, str) or not dataset_urn:
            return
        now = time.monotonic()
        self._recent_datasets[dataset_urn] = now
        # Ensure touch moves the URN to the end so prune removes the oldest first.
        self._recent_datasets.move_to_end(dataset_urn)
        self._prune_recent_datasets(now)

    def _prune_recent_datasets(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        cutoff = now - self._RECENT_DATASET_TTL_SECONDS

        while self._recent_datasets:
            oldest_urn, oldest_ts = next(iter(self._recent_datasets.items()))
            if (
                oldest_ts >= cutoff
                and len(self._recent_datasets) <= self._RECENT_DATASET_MAX
            ):
                break
            self._recent_datasets.popitem(last=False)

    def _dataset_recently_changed(self, dataset_urn: str) -> bool:
        if not isinstance(dataset_urn, str) or not dataset_urn:
            return False
        ts = self._recent_datasets.get(dataset_urn)
        if ts is None:
            return False
        now = time.monotonic()
        if now - ts > self._RECENT_DATASET_TTL_SECONDS:
            self._recent_datasets.pop(dataset_urn, None)
            self._prune_recent_datasets(now)
            return False
        return True

    def _prepare_request_kwargs(
        self, skip_auth: bool, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        else:
            kwargs["headers"] = dict(kwargs["headers"] or {})

        if self._default_headers:
            existing = {str(key).lower() for key in kwargs["headers"]}
            for key, value in self._default_headers.items():
                if key.lower() not in existing:
                    kwargs["headers"][key] = value
                    existing.add(key.lower())

        if self.authenticator is not None and not skip_auth:
            self.authenticator.authenticate(self.session)

        # Always inject session.verify when the caller hasn't supplied an
        # explicit verify kwarg.  This prevents requests'
        # merge_environment_settings from overriding session.verify with
        # REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE – whether session.verify is
        # False (SSL disabled) or a custom CA path set at runtime.
        if "verify" not in kwargs:
            kwargs["verify"] = self.session.verify

        return kwargs

    def _schema_retry_context(
        self, method: str, path: str, kwargs: dict[str, Any]
    ) -> tuple[str | None, bool]:
        dataset_urn_for_schema: str | None = None
        params = kwargs.get("params")
        if (
            isinstance(params, dict)
            and path.rstrip("/") == "catalog/datasets/by-urn/schema"
        ):
            urn = params.get("urn")
            dataset_urn_for_schema = urn if isinstance(urn, str) and urn else None

        schema_retry = (
            method.upper() == "PUT"
            and dataset_urn_for_schema is not None
            and self._dataset_recently_changed(dataset_urn_for_schema)
        )
        return dataset_urn_for_schema, schema_retry

    def _send_request(self, method: str, url: str, kwargs: dict[str, Any]):
        try:
            self.logger.debug(f"Request headers: {self.session.headers}")
            response = self.session.request(method, url, **kwargs)
            self.logger.debug(f"Response status: {response.status_code}")
            return response
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise APIError(f"An error occurred while making the request: {e}")

    def _handle_unauthorized_response(
        self,
        response,
        endpoint: str,
        skip_auth: bool,
        did_refresh: bool,
    ) -> None:
        """Handle a 401 response.  Always raises or returns None (refresh succeeded)."""
        if skip_auth:
            raise AuthenticationError(
                f"Unauthenticated request failed for {endpoint}: "
                f"{_extract_server_detail(response)}"
            )

        logger.warning(
            f"Received 401 Unauthorized. Response: {_extract_server_detail(response)}"
        )
        if self.authenticator is None:
            raise AuthenticationError(
                "Authentication failed. No authenticator provided."
            )

        can_refresh_attr = getattr(self.authenticator, "can_refresh", True)
        can_refresh = (
            can_refresh_attr() if callable(can_refresh_attr) else bool(can_refresh_attr)
        )
        if can_refresh and not did_refresh:
            self.authenticator.refresh_token(self.session)
            return
        if did_refresh:
            raise AuthenticationError(
                f"Authentication failed after token refresh for "
                f"{endpoint}: {_extract_server_detail(response)}"
            )
        raise AuthenticationError(
            f"Authentication failed for {endpoint}: {_extract_server_detail(response)}"
        )

    def _retry_dataset_schema_update(
        self,
        response,
        schema_retry: bool,
        retry_idx: int,
        dataset_urn_for_schema: str | None,
    ) -> tuple[bool, int]:
        if (
            not schema_retry
            or response.status_code != 404
            or retry_idx >= len(self._DATASET_SCHEMA_PUT_RETRY_DELAYS_SECONDS)
        ):
            return False, retry_idx

        content_type = response.headers.get("content-type", "")
        payload: Any | None = None
        if "application/json" in content_type.lower():
            try:
                payload = response.json()
            except ValueError:
                payload = None

        detail = payload.get("detail") if isinstance(payload, dict) else None
        if detail != "Dataset not found or schema could not be updated":
            return False, retry_idx

        delay = self._DATASET_SCHEMA_PUT_RETRY_DELAYS_SECONDS[retry_idx]
        next_retry_idx = retry_idx + 1
        self.logger.debug(
            "Retrying dataset schema update after 404 (attempt %s/%s, delay=%.2fs): %s",
            next_retry_idx,
            len(self._DATASET_SCHEMA_PUT_RETRY_DELAYS_SECONDS),
            delay,
            dataset_urn_for_schema,
        )
        time.sleep(delay)
        return True, next_retry_idx

    def _raise_for_error_response(self, response, path: str) -> None:
        content_type = response.headers.get("content-type", "")
        response_text = response.text
        payload: Any | None = None
        if "application/json" in content_type.lower():
            try:
                payload = response.json()
            except ValueError:
                payload = None

        if response.status_code == 404:
            lowered = content_type.lower()
            if "text/html" in lowered or "Dashboard" in response_text:
                raise NonAPIResponseError(
                    f"Received 404 with HTML response. "
                    f"Your base URL is '{self.base_url}' - did you forget to append '/api'?"
                )

        message = (
            f"API request failed with status {response.status_code}: {response_text}"
        )
        if response.status_code == 501 and (
            path.startswith("vectordb") or path.startswith("context/vectordb")
        ):
            raise VectorDBUnavailableError(
                "VectorDB backend is not configured",
                status_code=response.status_code,
                response_text=response_text,
                response_data=payload,
            )

        self.logger.error(f"Request failed: {response_text}")

        # T7.14 / PR feedback H2: route recognized server reasons through
        # the typed-exception dispatch (kamiwaza_sdk.exceptions.error_for_response)
        # so federation-aware subclasses surface to customers. Falls back to
        # the generic APIError when the (status_code, detail.reason) pair
        # has no typed mapping.
        from .exceptions import KamiwazaError, error_for_response

        typed = error_for_response(response.status_code, payload, message)
        if type(typed) is not KamiwazaError:
            if isinstance(typed, APIError):
                typed.response_text = response_text
                typed.response_data = payload
                typed.body = payload
            raise typed

        raise APIError(
            message,
            status_code=response.status_code,
            response_text=response_text,
            response_data=payload,
        )

    def _parse_response(self, response, expect_json: bool):
        if not expect_json:
            return response

        if response.status_code == 204:
            return None

        if 200 <= response.status_code < 300:
            try:
                return response.json()
            except ValueError:
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type or "Dashboard" in response.text:
                    raise NonAPIResponseError(
                        f"Received HTML response instead of JSON. "
                        f"Your base URL is '{self.base_url}' - did you forget to append '/api'?"
                    )
                raise APIError(
                    f"Failed to parse JSON response. Content-Type: {content_type}, "
                    f"Response: {response.text[:200]}...",
                    status_code=response.status_code,
                    response_text=response.text,
                )

        if response.status_code == 404:
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type or "Dashboard" in response.text:
                raise NonAPIResponseError(
                    f"Received 404 with HTML response. "
                    f"Your base URL is '{self.base_url}' - did you forget to append '/api'?"
                )
        raise APIError(
            f"Unexpected status code {response.status_code}: {response.text}",
            status_code=response.status_code,
            response_text=response.text,
        )

    def _assert_same_host(self, base_url: str) -> None:
        """Require base_url to share the platform's scheme/host/port.

        The platform bearer is attached to every request, so an off-host
        base_url would leak the credential. In-cluster extensions (Kaizen)
        share the platform ingress, so this never fires in normal use.
        """
        from urllib.parse import urlparse

        _default_ports = {"https": 443, "http": 80}

        def _origin(url: str) -> tuple:
            p = urlparse(url)
            return (p.scheme, p.hostname, p.port or _default_ports.get(p.scheme))

        if _origin(base_url) != _origin(self.base_url):
            home = urlparse(self.base_url)
            raise ValueError(
                f"base_url '{base_url}' is not on the platform host "
                f"'{home.scheme}://{home.netloc}'; refusing to send the platform "
                "credential off-host."
            )

    def _wait_for_psk_retry(self, kwargs: dict, state: "_RetryState") -> bool:
        """Sleep the next psk backoff step; False when the arm is exhausted.

        A non-replayable body opts out entirely: ``requests`` has already read
        it to EOF, so re-issuing would silently send a truncated payload.
        """
        if state.psk_idx >= len(_RETRY_BACKOFF_SCHEDULE_SECONDS):
            return False
        if not _request_body_is_replayable(kwargs):
            return False
        delay = _RETRY_BACKOFF_SCHEDULE_SECONDS[state.psk_idx]
        if time.monotonic() + delay > state.psk_deadline:
            return False
        time.sleep(delay)
        state.psk_idx += 1
        return True

    def _wait_for_greymatter_route_retry(
        self, response, kwargs: dict, state: "_RetryState"
    ) -> bool:
        """Wait for a read-only request fenced by Gray Matter route churn."""
        if not _is_greymatter_route_transient(response, state.method):
            return False
        if not _request_body_is_replayable(kwargs):
            return False
        schedule = _GREYMATTER_ROUTE_RETRY_SCHEDULE_SECONDS
        if state.greymatter_route_idx >= len(schedule):
            return False
        delay = schedule[state.greymatter_route_idx]
        state.greymatter_route_idx += 1
        self.logger.debug(
            "Retrying %s %s after Gray Matter route transient (delay=%.1fs)",
            state.method,
            state.path,
            delay,
        )
        time.sleep(delay)
        return True

    def _wait_for_retryable_503(
        self, response, kwargs: dict, state: "_RetryState"
    ) -> bool:
        """Sleep for a recognized 503 retry signal; False when not retryable.

        Gray Matter route churn has its own bounded schedule. Server-directed
        Retry-After hints are clamped and jittered so co-fenced clients do not
        retry in lockstep. False preserves the normal terminal error path.
        """
        if self._wait_for_greymatter_route_retry(response, kwargs, state):
            return True
        retry_after = _retry_after_seconds(response)
        if retry_after is None:
            return False
        if retry_after > _RETRYABLE_503_WALL_CLOCK_BUDGET_SECONDS:
            # Core asked for longer than we are willing to wait in total, so
            # any retry we make is premature by construction and lands inside
            # the window it was told to sit out. auto_provisioner's
            # _BACKOFF_SCHEDULE reaches 60/300/900; clamping those to 30 buys
            # exactly one guaranteed-to-fail replay and ~31.5s of latency on a
            # call that used to fail fast. Surface the hint instead.
            return False
        if not _request_body_is_replayable(kwargs):
            return False
        if state.attempts_503 >= _RETRYABLE_503_MAX_ATTEMPTS:
            return False
        # Order matters: jitter is added *before* the ceiling clamp. Clamping
        # first lets jitter lift the result to 30 * 1.1 = 33.0, which both
        # breaks the documented ceiling and, at a hint of exactly 30 (what
        # core sends for three of the admitted codes), spends the 60s budget
        # in one sleep instead of two.
        delay = max(retry_after, _RETRYABLE_503_MIN_SLEEP_SECONDS)
        delay += delay * _RETRYABLE_503_JITTER_FRACTION * _retry_jitter_unit()
        delay = min(delay, _RETRYABLE_503_MAX_SLEEP_SECONDS)
        if time.monotonic() + delay > state.deadline_503:
            return False
        self.logger.debug(
            "Retrying %s %s after server Retry-After=%.1fs",
            state.method,
            state.path,
            delay,
        )
        time.sleep(delay)
        state.attempts_503 += 1
        return True

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        expect_json: bool = True,
        skip_auth: bool = False,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        # base_url targets an in-cluster extension (e.g. Kaizen) on the platform
        # ingress; it must stay same-host since the platform bearer is attached
        # to every request.
        if base_url is not None:
            self._assert_same_host(base_url)
        root = (base_url or self.base_url).rstrip("/")
        url = f"{root}/{endpoint.lstrip('/')}"
        path = endpoint.lstrip("/")
        self.logger.debug(f"Making {method} request to {url}")
        kwargs = self._prepare_request_kwargs(skip_auth, kwargs)
        dataset_urn_for_schema, schema_retry = self._schema_retry_context(
            method, path, kwargs
        )
        retry_idx = 0
        did_refresh = False

        # T7.4 / ENG-5038: psk_propagation_timeout retry state per design §4.2.1.
        # Deadline captures wall-clock budget at request entry; schedule_idx
        # advances exactly once per retry so the (1, 2, 4, 8, 16, 32, 64)
        # progression is preserved across the loop.
        state = _RetryState(method, path)

        while True:
            response = self._send_request(method, url, kwargs)

            if response.status_code == 401:
                self._handle_unauthorized_response(
                    response, endpoint, skip_auth, did_refresh
                )
                if not _request_body_is_replayable(kwargs):
                    # The refresh succeeded, but the body is spent. Replaying
                    # would re-send it from EOF and silently write an empty or
                    # truncated file -- the most reachable form of this in
                    # production is an upload spanning a token expiry.
                    raise AuthenticationError(
                        "Authentication was refreshed, but the request body is "
                        "a stream that has already been consumed and cannot be "
                        "safely re-sent. Retry the call with a fresh handle.",
                        status_code=response.status_code,
                    )
                did_refresh = True
                continue

            if _is_psk_propagation_timeout(response):
                # Retry within budget, otherwise raise the typed
                # FederationPairTimeoutError so customer code can branch on the
                # specific terminal failure.
                if self._wait_for_psk_retry(kwargs, state):
                    continue
                raise _psk_timeout_error_from_response(response)

            if self._wait_for_retryable_503(response, kwargs, state):
                continue

            if response.status_code >= 400:
                should_retry, retry_idx = self._retry_dataset_schema_update(
                    response, schema_retry, retry_idx, dataset_urn_for_schema
                )
                if should_retry:
                    continue
                self._raise_for_error_response(response, path)

            break

        return self._parse_response(response, expect_json)

    def get(self, endpoint: str, **kwargs):
        return self._request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs):
        return self._request("POST", endpoint, **kwargs)

    def put(self, endpoint: str, **kwargs):
        return self._request("PUT", endpoint, **kwargs)

    def delete(self, endpoint: str, **kwargs):
        return self._request("DELETE", endpoint, **kwargs)

    def patch(self, endpoint: str, **kwargs):
        return self._request("PATCH", endpoint, **kwargs)

    def workroom_scope(self, workroom_id: Any | None) -> "KamiwazaClient":
        """Return a client whose requests target ``workroom_id``.

        Scopes a local SDK client instance to the specified workroom id by
        adding the explicit workroom scope header to each request. ``None``
        returns a client with no workroom scope. Client-only: this does not call
        ``workrooms.enter`` and does not mutate server-side selected-session
        binding or the parent client.
        """
        scoped = type(self)(
            base_url=self.base_url,
            authenticator=self.authenticator,
            verify=self.session.verify,
        )
        # Preserve exact parent auth state; __init__ may otherwise consult env vars.
        scoped.authenticator = self.authenticator
        scoped._owned_authenticator = None
        scoped._owns_authenticator = False
        scoped.session.headers.update(self.session.headers)
        scoped.session.cookies.update(self.session.cookies)
        scoped._default_headers = dict(self._default_headers)
        if workroom_id is None:
            scoped._default_headers.pop(_WORKROOM_SCOPE_HEADER, None)
        else:
            scoped._default_headers[_WORKROOM_SCOPE_HEADER] = str(workroom_id)
        return scoped

    # Lazy load the services
    @property
    def models(self):
        if not hasattr(self, "_models"):
            self._models = ModelService(self)
        return self._models

    @property
    def serving(self):
        if not hasattr(self, "_serving"):
            self._serving = ServingService(self)
        return self._serving

    @property
    def catalog(self):
        if not hasattr(self, "_catalog"):
            self._catalog = CatalogService(self)
        return self._catalog

    @property
    def prompts(self):
        if not hasattr(self, "_prompts"):
            self._prompts = PromptsService(self)
        return self._prompts

    @property
    def embedding(self):
        if not hasattr(self, "_embedding"):
            self._embedding = EmbeddingService(self)
        return self._embedding

    @property
    def cluster(self):
        """Cluster operations — legacy CRUD + federation-aware surfaces.

        Returns a ``ClusterAPI`` instance (T7.7 / ENG-5041 federation-aware
        cluster service). ClusterAPI inherits from ClusterService, so
        existing legacy methods (``list_locations``, ``list_clusters``, etc.)
        continue to work alongside the M3+ methods (``capabilities``,
        ``set_execution_gate``, ``declare_attribute``, etc.).
        """
        if not hasattr(self, "_cluster"):
            from .services.cluster_federation import ClusterAPI

            self._cluster = ClusterAPI(self)
        return self._cluster

    # T7.5/T7.6/T7.8/T7.9/T7.10/T7.11 lazy-property wires for federation-aware
    # services. Per OQ-16 design v0.3.7: lazy-load pattern applied consistently.

    @property
    def federations(self):
        """Federation pairing + brokered-user management (T7.5 / ENG-5039)."""
        if not hasattr(self, "_federations"):
            from .services.federations import FederationsAPI

            self._federations = FederationsAPI(self)
        return self._federations

    @property
    def jobs(self):
        """Federated job submission (T7.6 / ENG-5040)."""
        if not hasattr(self, "_jobs"):
            from .services.jobs_federation import JobsAPI

            self._jobs = JobsAPI(self)
        return self._jobs

    @property
    def subjects(self):
        """AuthzSubjects + grants surface (T7.8 / ENG-5042)."""
        if not hasattr(self, "_subjects"):
            from .services.subjects import SubjectsAPI

            self._subjects = SubjectsAPI(self)
        return self._subjects

    @property
    def datasets(self):
        """Catalog datasets + attribute-gate binding (T7.9 / ENG-5043)."""
        if not hasattr(self, "_datasets"):
            from .services.datasets import DatasetsAPI

            self._datasets = DatasetsAPI(self)
        return self._datasets

    @property
    def gates(self):
        """Gate discovery (T7.10 / ENG-5044)."""
        if not hasattr(self, "_gates"):
            from .services.gates import GatesAPI

            self._gates = GatesAPI(self)
        return self._gates

    @property
    def activity(self):
        if not hasattr(self, "_activity"):
            self._activity = ActivityService(self)
        return self._activity

    @property
    def lab(self):
        if not hasattr(self, "_lab"):
            self._lab = LabService(self)
        return self._lab

    @property
    def auth(self):
        return self._auth_service

    @property
    def authz(self):
        if not hasattr(self, "_authz"):
            self._authz = AuthzService(self)
        return self._authz

    def get_bearer_token(self) -> Optional[str]:
        if self.authenticator is None:
            return None
        try:
            return self.authenticator.get_access_token(self.session)
        except AttributeError:
            return None

    @property
    def retrieval(self):
        """Retrieval — legacy streaming surface + federation-aware list/cancel.

        Returns a ``RetrievalAPI`` instance (T7.11 / ENG-5045). RetrievalAPI
        inherits from RetrievalService, so existing legacy methods
        (``create_job``, ``materialize``, ``stream_events``) continue to
        work alongside the M3 methods (``list``, ``cancel``).
        """
        if not hasattr(self, "_retrieval"):
            from .services.retrieval_federation import RetrievalAPI

            self._retrieval = RetrievalAPI(self)
        return self._retrieval

    @property
    def openai(self):
        if not hasattr(self, "_openai"):
            self._openai = OpenAIService(self)
        return self._openai

    @property
    def apps(self):
        if not hasattr(self, "_apps"):
            self._apps = AppService(self)
        return self._apps

    @property
    def tools(self):
        if not hasattr(self, "_tools"):
            self._tools = ToolService(self)
        return self._tools

    @property
    def ingestion(self):
        if not hasattr(self, "_ingestion"):
            self._ingestion = IngestionService(self)
        return self._ingestion

    @property
    def context(self):
        if not hasattr(self, "_context"):
            self._context = ContextService(self)
        return self._context

    @property
    def skills(self):
        if not hasattr(self, "_skills"):
            self._skills = SkillsService(self)
        return self._skills

    @property
    def extensions(self):
        if not hasattr(self, "_extensions"):
            from .services.extensions import ExtensionService

            self._extensions = ExtensionService(self)
        return self._extensions

    @property
    def enclaves(self):
        if not hasattr(self, "_enclaves"):
            self._enclaves = EnclavesService(self)
        return self._enclaves

    @property
    def workrooms(self):
        if not hasattr(self, "_workrooms"):
            self._workrooms = WorkroomService(self)
        return self._workrooms

    @property
    def connectors(self):
        """Cluster-wide external connectors (M365, Google, …)."""
        if not hasattr(self, "_connectors"):
            from .services.connectors import ConnectorService

            self._connectors = ConnectorService(self)
        return self._connectors

    @property
    def agents(self):
        """Kaizen agents (per-workroom extension; methods take a base_url)."""
        if not hasattr(self, "_agents"):
            from .services.kaizen import AgentService

            self._agents = AgentService(self)
        return self._agents

    @property
    def conversations(self):
        """Kaizen conversations (per-workroom extension; methods take a base_url)."""
        if not hasattr(self, "_conversations"):
            from .services.kaizen import ConversationService

            self._conversations = ConversationService(self)
        return self._conversations

    @property
    def kaizen_ops(self):
        """Kaizen operator settings (per-workroom extension; take a base_url)."""
        if not hasattr(self, "_kaizen_ops"):
            from .services.kaizen import KaizenOpsService

            self._kaizen_ops = KaizenOpsService(self)
        return self._kaizen_ops
