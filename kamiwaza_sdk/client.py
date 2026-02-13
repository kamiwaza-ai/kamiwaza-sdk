# kamiwaza_sdk/client.py

from collections import OrderedDict
import logging
import os
import time
from typing import Any, Optional

import requests

from .exceptions import (
    APIError,
    AuthenticationError,
    NonAPIResponseError,
    VectorDBUnavailableError,
)
from .services.models import ModelService
from .services.serving import ServingService
from .services.vectordb import VectorDBService
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
from .services.oauth_broker import OAuthBrokerService

logger = logging.getLogger(__name__)

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
    ):
        # Configure logging
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logger
        
        resolved_base_url = base_url or os.environ.get("KAMIWAZA_BASE_URL") or os.environ.get("KAMIWAZA_BASE_URI")
        if not resolved_base_url:
            raise ValueError(
                "base_url is required. Provide it directly or set KAMIWAZA_BASE_URL or KAMIWAZA_BASE_URI."
            )

        self.base_url = resolved_base_url.rstrip('/')
        self.session = requests.Session()
        self._recent_datasets: "OrderedDict[str, float]" = OrderedDict()
        
        # Check KAMIWAZA_VERIFY_SSL environment variable
        verify_ssl = os.environ.get('KAMIWAZA_VERIFY_SSL', 'true').lower()
        if verify_ssl == 'false':
            self.session.verify = False
            # Suppress SSL warnings when verification is disabled
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self.logger.info("SSL verification disabled (KAMIWAZA_VERIFY_SSL=false)")
        
        # Initialize _auth_service directly
        self._auth_service = AuthService(self)

        if authenticator:
            self.authenticator = authenticator
        else:
            api_key = api_key or os.environ.get("KAMIWAZA_API_KEY") or os.environ.get("KAMIWAZA_API_TOKEN")
            self.authenticator = ApiKeyAuthenticator(api_key) if api_key else None
        
        # Don't authenticate during initialization - let it happen on first request

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
            if oldest_ts >= cutoff and len(self._recent_datasets) <= self._RECENT_DATASET_MAX:
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

        # Ensure headers are present
        if "headers" not in kwargs:
            kwargs["headers"] = {}

        # Ensure authentication is set up (except for auth endpoints)
        if self.authenticator and not skip_auth:
            self.authenticator.authenticate(self.session)

        params = kwargs.get("params") if isinstance(kwargs.get("params"), dict) else {}
        dataset_urn_for_schema = (
            params.get("urn") if path.rstrip("/") == "catalog/datasets/by-urn/schema" else None
        )
        schema_retry = (
            method.upper() == "PUT"
            and dataset_urn_for_schema
            and self._dataset_recently_changed(str(dataset_urn_for_schema))
        )
        retry_idx = 0
        did_refresh = False

        while True:
            try:
                # Debug headers
                self.logger.debug(f"Request headers: {self.session.headers}")
                response = self.session.request(method, url, **kwargs)
                self.logger.debug(f"Response status: {response.status_code}")
            except requests.RequestException as e:
                logger.error(f"Request failed: {e}")
                raise APIError(f"An error occurred while making the request: {e}")

            if response.status_code == 401:
                if skip_auth:
                    raise AuthenticationError(
                        f"Unauthenticated request failed for {endpoint}: {response.text}"
                    )
                logger.warning(f"Received 401 Unauthorized. Response: {response.text}")
                if self.authenticator:
                    if not did_refresh:
                        did_refresh = True
                        self.authenticator.refresh_token(self.session)
                        continue
                    raise AuthenticationError("Authentication failed after token refresh.")
                raise AuthenticationError("Authentication failed. No authenticator provided.")

            if response.status_code >= 400:
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

                if schema_retry and response.status_code == 404 and retry_idx < len(
                    self._DATASET_SCHEMA_PUT_RETRY_DELAYS_SECONDS
                ):
                    detail = payload.get("detail") if isinstance(payload, dict) else None
                    if detail == "Dataset not found or schema could not be updated":
                        delay = self._DATASET_SCHEMA_PUT_RETRY_DELAYS_SECONDS[retry_idx]
                        retry_idx += 1
                        self.logger.debug(
                            "Retrying dataset schema update after 404 (attempt %s/%s, delay=%.2fs): %s",
                            retry_idx,
                            len(self._DATASET_SCHEMA_PUT_RETRY_DELAYS_SECONDS),
                            delay,
                            dataset_urn_for_schema,
                        )
                        time.sleep(delay)
                        continue

                message = f"API request failed with status {response.status_code}: {response_text}"
                if response.status_code == 501 and path.startswith("vectordb"):
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

            break

        if not expect_json:
            return response

        if response.status_code == 204:
            return None

        if 200 <= response.status_code < 300:
            # Try to parse JSON
            try:
                return response.json()
            except ValueError:
                # Check if we got an HTML response (likely the dashboard)
                content_type = response.headers.get('content-type', '').lower()
                if 'text/html' in content_type or 'Dashboard' in response.text:
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

        # For non-2xx status codes, check if it's an HTML error page
        if response.status_code == 404:
            content_type = response.headers.get('content-type', '').lower()
            if 'text/html' in content_type or 'Dashboard' in response.text:
                raise NonAPIResponseError(
                    f"Received 404 with HTML response. "
                    f"Your base URL is '{self.base_url}' - did you forget to append '/api'?"
                )
        raise APIError(
            f"Unexpected status code {response.status_code}: {response.text}",
            status_code=response.status_code,
            response_text=response.text,
        )

    def get(self, endpoint: str, **kwargs):
        return self._request('GET', endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs):
        return self._request('POST', endpoint, **kwargs)

    def put(self, endpoint: str, **kwargs):
        return self._request('PUT', endpoint, **kwargs)

    def delete(self, endpoint: str, **kwargs):
        return self._request('DELETE', endpoint, **kwargs)

    def patch(self, endpoint: str, **kwargs):
        return self._request('PATCH', endpoint, **kwargs)

    # Lazy load the services
    @property
    def models(self):
        if not hasattr(self, '_models'):
            self._models = ModelService(self)
        return self._models

    @property
    def serving(self):
        if not hasattr(self, '_serving'):
            self._serving = ServingService(self)
        return self._serving

    @property
    def vectordb(self):
        if not hasattr(self, '_vectordb'):
            self._vectordb = VectorDBService(self)
        return self._vectordb
    

    @property
    def catalog(self):
        if not hasattr(self, '_catalog'):
            self._catalog = CatalogService(self)
        return self._catalog

    @property
    def prompts(self):
        if not hasattr(self, '_prompts'):
            self._prompts = PromptsService(self)
        return self._prompts

    @property
    def embedding(self):
        if not hasattr(self, '_embedding'):
            self._embedding = EmbeddingService(self)
        return self._embedding

    @property
    def cluster(self):
        if not hasattr(self, '_cluster'):
            self._cluster = ClusterService(self)
        return self._cluster

    @property
    def activity(self):
        if not hasattr(self, '_activity'):
            self._activity = ActivityService(self)
        return self._activity

    @property
    def lab(self):
        if not hasattr(self, '_lab'):
            self._lab = LabService(self)
        return self._lab

    @property
    def auth(self):
        return self._auth_service

    @property
    def authz(self):
        if not hasattr(self, '_authz'):
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
        if not hasattr(self, '_retrieval'):
            self._retrieval = RetrievalService(self)
        return self._retrieval

    @property
    def openai(self):
        if not hasattr(self, '_openai'):
            self._openai = OpenAIService(self)
        return self._openai
    
    @property
    def apps(self):
        if not hasattr(self, '_apps'):
            self._apps = AppService(self)
        return self._apps
    
    @property
    def tools(self):
        if not hasattr(self, '_tools'):
            self._tools = ToolService(self)
        return self._tools

    @property
    def ingestion(self):
        if not hasattr(self, '_ingestion'):
            self._ingestion = IngestionService(self)
        return self._ingestion

    @property
    def oauth_broker(self):
        if not hasattr(self, '_oauth_broker'):
            self._oauth_broker = OAuthBrokerService(self)
        return self._oauth_broker
