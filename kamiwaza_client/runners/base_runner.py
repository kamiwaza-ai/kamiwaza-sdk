# kamiwaza_client/runners/base_runner.py

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Union, Optional
from kamiwaza_client.services.base_service import BaseService

class BaseRunnerClient(BaseService, ABC):
    def __init__(self, client):
        super().__init__(client)
        self.dataset_details = None

    def dataset_details_from_datasets(self, datasets: Union[List[str], List[Dict], Dict, str]) -> List[Dict]:
        endpoint = f"{self.base_url}/runner/dataset_details"
        payload = {"datasets": datasets}
        response = self.client.post(endpoint, json=payload)
        return response.json()

    def get_dataset_credentials(self, **kwargs) -> Dict[str, Any]:
        endpoint = f"{self.base_url}/runner/dataset_credentials"
        response = self.client.post(endpoint, json=kwargs)
        return response.json()

    @abstractmethod
    def metadata(self, **kwargs) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_snapshot(self, **kwargs) -> Any:
        pass