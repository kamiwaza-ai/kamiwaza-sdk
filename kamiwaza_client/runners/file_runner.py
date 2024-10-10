# kamiwaza_client/runners/file_runner.py

from kamiwaza_client.runners.base_runner import BaseRunnerClient
from typing import List, Dict, Any, Optional, Union

class FileRunnerClient(BaseRunnerClient):
    def __init__(self, client):
        super().__init__(client)

    def metadata(self, **kwargs) -> Dict[str, Any]:
        endpoint = f"{self.base_url}/file_runner/metadata"
        response = self.client.post(endpoint, json=kwargs)
        return response.json()

    def get_snapshot(self, **kwargs) -> Any:
        endpoint = f"{self.base_url}/file_runner/snapshot"
        response = self.client.post(endpoint, json=kwargs)
        return response.json()

    def get_ray_datasets(self, datasets: Optional[Union[List[str], List[Dict], Dict, str]] = None, 
                         ray_mode: str = 'read_binary_files', **kwargs) -> Dict[str, Any]:
        endpoint = f"{self.base_url}/file_runner/ray_datasets"
        payload = {
            "datasets": datasets,
            "ray_mode": ray_mode,
            **kwargs
        }
        response = self.client.post(endpoint, json=payload)
        return response.json()

    def list_from_dataset(self, dataset_details: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None,
                          recursive: bool = True, **kwargs) -> List[Dict[str, Any]]:
        endpoint = f"{self.base_url}/file_runner/list_dataset"
        payload = {
            "dataset_details": dataset_details,
            "recursive": recursive,
            **kwargs
        }
        response = self.client.post(endpoint, json=payload)
        return response.json()

    def read_file(self, file_path: str, offset: int = 0, length: Optional[int] = None, **kwargs) -> bytes:
        endpoint = f"{self.base_url}/file_runner/read_file"
        params = {
            "file_path": file_path,
            "offset": offset,
            "length": length,
            **kwargs
        }
        response = self.client.get(endpoint, params=params)
        return response.content

    def retrieve_chunks(self, search_results: List[Dict], read_length: int = 4096, 
                        preread_bytes: int = 500, autodecode: bool = True) -> List[Dict[str, Any]]:
        endpoint = f"{self.base_url}/file_runner/retrieve_chunks"
        payload = {
            "search_results": search_results,
            "read_length": read_length,
            "preread_bytes": preread_bytes,
            "autodecode": autodecode
        }
        response = self.client.post(endpoint, json=payload)
        return response.json()