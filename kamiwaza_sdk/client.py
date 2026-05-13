# kamiwaza_sdk/client.py

from collections import OrderedDict
import logging
import os
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
from .services.cluster import ClusterService
from .services.activity import ActivityService
from .services.lab import LabService
from .services.auth import AuthService
from .services.authz import AuthzService
from .authentication import Authenticator, ApiKeyAuthenticator
from .services.retrieval import RetrievalService
from .services.ingestion import IngestionService
from .services.openai import OpenAIService
from .services.apps import AppService
from .services.tools import ToolService
from .services.context import ContextService
from .services.skills import SkillsService
from .services.enclaves import EnclavesService

logger = logging.getLogger(__name__)

_AUTH_ERROR_DETAIL_MAX_LEN = 500
_AUTH_ERROR_DETAIL_TRUNCATED_SUFFIX = "... [truncated]"
_VERIFY_SSL_FALSE_VALUES = {"false", "0", "no"}


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
        if authenticator:
            self.authenticator = authenticator
        else:
            api_key = (
                api_key
                or os.environ.get("KAMIWAZA_API_KEY")
                or os.environ.get("KAMIWAZA_API_TOKEN")
            )
            self.authenticator = ApiKeyAuthenticator(api_key) if api_key else None

        # Don't authenticate during initialization - let it happen on first request

    def close(self) -> None:
        """Release the underlying requests.Session transport.

        Idempotent — repeated close() calls are safe (Session.close()
        does its own idempotency).
        """
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

        if self.authenticator and not skip_auth:
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
        if not self.authenticator:
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

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        expect_json: bool = True,
        skip_auth: bool = False,
        **kwargs,
    ):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
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
        psk_deadline = time.monotonic() + _RETRY_WALL_CLOCK_BUDGET_SECONDS
        psk_schedule_idx = 0

        while True:
            response = self._send_request(method, url, kwargs)

            if response.status_code == 401:
                self._handle_unauthorized_response(
                    response, endpoint, skip_auth, did_refresh
                )
                did_refresh = True
                continue

            if _is_psk_propagation_timeout(response):
                # Eligible for psk retry — sleep + continue if budget allows,
                # otherwise raise the typed FederationPairTimeoutError so
                # customer code can branch on the specific terminal failure.
                if psk_schedule_idx < len(_RETRY_BACKOFF_SCHEDULE_SECONDS):
                    delay = _RETRY_BACKOFF_SCHEDULE_SECONDS[psk_schedule_idx]
                    if time.monotonic() + delay <= psk_deadline:
                        time.sleep(delay)
                        psk_schedule_idx += 1
                        continue
                raise _psk_timeout_error_from_response(response)

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
        if not hasattr(self, "_cluster"):
            self._cluster = ClusterService(self)
        return self._cluster

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
        if not self.authenticator:
            return None
        try:
            return self.authenticator.get_access_token(self.session)
        except AttributeError:
            return None

    @property
    def retrieval(self):
        if not hasattr(self, "_retrieval"):
            self._retrieval = RetrievalService(self)
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
