# kamiwaza_client/client.py

import requests
from typing import Optional
from .exceptions import APIError, AuthenticationError
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
from .authentication import Authenticator, ApiKeyAuthenticator
from .services.retrieval import RetrievalService
from .services.openai import OpenAIService
from .services.apps import AppService
from .services.tools import ToolService
import logging

logger = logging.getLogger(__name__)

class KamiwazaClient:
    def __init__(
        self,
        base_url: str,
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
        
        if not base_url:
            raise ValueError("base_url is required. Please set KAMIWAZA_API_URI environment variable or provide the base_url directly.")
            
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        
        # Initialize _auth_service directly
        self._auth_service = AuthService(self)

        if authenticator:
            self.authenticator = authenticator
        elif api_key:
            self.authenticator = ApiKeyAuthenticator(api_key)
        else:
            self.authenticator = None
        
        # Don't authenticate during initialization - let it happen on first request

    def _request(self, method: str, endpoint: str, **kwargs):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        self.logger.debug(f"Making {method} request to {url}")

        # Ensure headers are present
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        
        # Ensure authentication is set up (except for auth endpoints)
        if self.authenticator and endpoint != "/auth/token":
            self.authenticator.authenticate(self.session)

        try:
            # Debug headers
            self.logger.debug(f"Request headers: {self.session.headers}")
            response = self.session.request(method, url, **kwargs)
            self.logger.debug(f"Response status: {response.status_code}")
            
            if response.status_code == 401:
                # Don't try to refresh token if we're already calling the auth endpoint
                if endpoint == "/auth/token":
                    self.logger.error(f"Login endpoint returned 401: {response.text}")
                    raise APIError(f"Login failed with status {response.status_code}: {response.text}")
                    
                logger.warning(f"Received 401 Unauthorized. Response: {response.text}")
                if self.authenticator:
                    self.authenticator.refresh_token(self.session)
                    response = self.session.request(method, url, **kwargs)
                    if response.status_code == 401:
                        raise AuthenticationError("Authentication failed after token refresh.")
                else:
                    raise AuthenticationError("Authentication failed. No authenticator provided.")
            elif response.status_code >= 400:
                self.logger.error(f"Request failed: {response.text}")
                raise APIError(f"API request failed with status {response.status_code}: {response.text}")
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise APIError(f"An error occurred while making the request: {e}")
        
        # Check if response is successful before parsing JSON
        if response.status_code == 200:
            return response.json()
        else:
            raise APIError(f"Unexpected status code {response.status_code}: {response.text}")

    def get(self, endpoint: str, **kwargs):
        return self._request('GET', endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs):
        return self._request('POST', endpoint, **kwargs)

    def put(self, endpoint: str, **kwargs):
        return self._request('PUT', endpoint, **kwargs)

    def delete(self, endpoint: str, **kwargs):
        return self._request('DELETE', endpoint, **kwargs)

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

