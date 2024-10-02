from typing import List, Dict, Any
from ..exceptions import NotFoundError, APIError

class ModelService:
    def __init__(self, client):
        self.client = client

    def list_models(self) -> List[Dict[str, Any]]:
        """List all available models"""
        return self.client.get('models')

    def create_model(self, name: str, **kwargs) -> Dict[str, Any]:
        """Create a new model"""
        data = {"name": name, **kwargs}
        return self.client.post('models', json=data)

    def get_model(self, model_id: str) -> Dict[str, Any]:
        """Get a specific model by ID"""
        try:
            return self.client.get(f'models/{model_id}')
        except APIError as e:
            if e.response.status_code == 404:
                raise NotFoundError(f"Model with ID {model_id} not found")
            raise

    def delete_model(self, model_id: str) -> None:
        """Delete a specific model by ID"""
        self.client.delete(f'models/{model_id}')

    # Add more methods as needed