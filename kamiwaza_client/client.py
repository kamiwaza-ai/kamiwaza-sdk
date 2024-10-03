# kamiwaza_client/client.py
import requests
from typing import Optional
from .exceptions import APIError, AuthenticationError
from .services.models import ModelService
from .services.serving import ServingService
from .services.vectordb import VectorDBService
from .services.catalog import CatalogService
from .services.prompts import PromptService
from .services.embedding import EmbeddingService
from .services.cluster import ClusterService
from .services.activity import ActivityService
from .services.lab import LabService
from .services.auth import AuthService

class KamiwazaClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({'Authorization': f'Bearer {api_key}'})

    def _request(self, method: str, endpoint: str, **kwargs):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = self.session.request(method, url, **kwargs)
        
        if response.status_code == 401:
            raise AuthenticationError("Authentication failed. Please check your API key.")
        elif response.status_code >= 400:
            raise APIError(f"API request failed with status {response.status_code}: {response.text}")

        return response.json()

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
            self._prompts = PromptService(self)
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
        if not hasattr(self, '_auth'):
            self._auth = AuthService(self)
        return self._auth