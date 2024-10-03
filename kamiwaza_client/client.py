# kamiwaza_client/client.py

import requests
from typing import Optional
from .exceptions import APIError, AuthenticationError
from .services.models import ModelService
from .services.serving import ServingService
class KamiwazaClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({'Authorization': f'Bearer {api_key}'})

        # Initalize services
        self.models = ModelService(self)
        self.serving = ServingService(self)

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

    # Add service properties here as we implement them
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