# kamiwaza_client/services/embedding.py

from typing import List, Optional, Any, Dict, Union
from uuid import UUID
from ..schemas.embedding import EmbeddingInput, EmbeddingOutput, EmbeddingConfig, ChunkResponse
from .base_service import BaseService
from ..exceptions import APIError
import logging

logger = logging.getLogger(__name__)

class EmbeddingProvider:
    """Provider class for handling embedder-specific operations"""
    
    def __init__(self, service: 'EmbeddingService', embedder_id: Union[str, UUID]):
        self._service = service
        self.embedder_id = str(embedder_id)
        self.default_timeout = 60
        self.batch_timeout = 120  # 2 minutes for batch operations
        self.model_load_timeout = 300  # 5 minutes for first model load

    def chunk_text(
        self, 
        text: str, 
        max_length: int = 510, 
        overlap: int = 32,
        preamble_text: str = "",
        return_metadata: bool = False,
        timeout: Optional[int] = None
    ) -> Union[List[str], ChunkResponse]:
        """Chunk text into smaller pieces."""
        # Parameter validation
        if max_length < 100:
            max_length = 510
        if overlap >= max_length // 2:
            overlap = max_length // 4
            
        params = {
            "text": text,
            "max_length": max_length,
            "overlap": overlap,
            "preamble_text": preamble_text,
            "embedder_id": self.embedder_id,
            "return_metadata": return_metadata
        }
        
        try:
            response = self._service.client.post(
                "/embedding/chunk", 
                params=params,
                timeout=timeout or self.default_timeout
            )
            
            if return_metadata:
                return ChunkResponse(
                    chunks=response["chunks"],
                    offsets=response.get("offsets"),
                    token_counts=response.get("token_counts")
                )
            return response
        except Exception as e:
            if "timeout" in str(e).lower():
                raise TimeoutError(f"Request timed out after {timeout or self.default_timeout} seconds")
            raise APIError(f"Operation failed: {str(e)}")

    def embed_chunks(self, text_chunks: List[str], batch_size: int = 64, timeout: Optional[int] = None) -> List[List[float]]:
        """Generate embeddings for a list of text chunks."""
        try:
            actual_timeout = timeout or self.batch_timeout
            return self._service.client.post(
                "/embedding/batch", 
                params={"batch_size": batch_size, "embedder_id": self.embedder_id},
                json=text_chunks,
                timeout=actual_timeout
            )
        except Exception as e:
            if "timeout" in str(e).lower():
                raise TimeoutError(f"Batch embedding timed out after {actual_timeout} seconds")
            raise APIError(f"Operation failed: {str(e)}")

    def create_embedding(self, text: str, max_length: int = 382,
                        overlap: int = 32, preamble_text: str = "") -> EmbeddingOutput:
        """Create an embedding for the given text."""
        input_data = EmbeddingInput(
            id=self.embedder_id,
            text=text,
            max_length=max_length,
            overlap=overlap,
            preamble_text=preamble_text
        )
        try:
            timeout = self.model_load_timeout if not self._service._model_loaded.get(self.embedder_id) else self.default_timeout
            response = self._service.client.post(
                "/embedding/generate", 
                json=input_data.model_dump(),
                timeout=timeout
            )
            self._service._model_loaded[self.embedder_id] = True
            return EmbeddingOutput.model_validate(response)
        except Exception as e:
            if "timeout" in str(e).lower():
                raise TimeoutError(f"Request timed out after {timeout} seconds")
            raise APIError(f"Operation failed: {str(e)}")

    def get_embedding(self, text: str, return_offset: bool = False) -> EmbeddingOutput:
        """Get an embedding for the given text."""
        response = self._service.client.get(
            f"/embedding/generate/{text}",
            params={
                "embedder_id": self.embedder_id,
                "return_offset": return_offset
            }
        )
        return EmbeddingOutput.model_validate(response)

    def reset_model(self) -> Dict[str, str]:
        """Reset the embedding model."""
        try:
            return self._service.client.post(
                "/embedding/reset",
                params={"embedder_id": self.embedder_id},
                timeout=self.default_timeout
            )
        except Exception as e:
            raise APIError(f"Failed to reset model: {str(e)}")

    def call(self, batch: Dict[str, List[Any]], model_name: Optional[str] = None) -> Dict[str, List[Any]]:
        """Generate embeddings for a batch of inputs."""
        params = {
            "embedder_id": self.embedder_id,
            "model_name": model_name
        }
        return self._service.client.post("/embedding/embedding/call", 
                                       params=params, 
                                       json=batch)

    def __del__(self):
        """Cleanup when provider is destroyed"""
        try:
            self._service.client.delete(f"/embedding/{self.embedder_id}")
        except:
            pass

class EmbeddingService(BaseService):
    """Main service class for managing embedding operations"""

    def __init__(self, client):
        super().__init__(client)
        self.default_timeout = 60
        self.init_timeout = 300  # 5 minutes for initialization
        self._model_loaded = {}  # Track which models have been loaded

    def initialize_provider(
        self, 
        provider_type: str, 
        model: str, 
        device: Optional[str] = None,
        timeout: Optional[int] = None,
        **kwargs
    ) -> EmbeddingProvider:
        """Initialize a new embedding provider"""
        config = EmbeddingConfig(
            provider_type=provider_type,
            model=model,
            device=device,
            **kwargs
        )
        try:
            actual_timeout = timeout or self.init_timeout
            response = self.client.post(
                "/embedding/initialize", 
                json=config.model_dump(),
                timeout=actual_timeout
            )
            provider_id = response["id"]
            self._model_loaded[provider_id] = False  # Track new provider
            return EmbeddingProvider(self, provider_id)
        except Exception as e:
            if "timeout" in str(e).lower():
                raise TimeoutError(
                    f"Provider initialization timed out after {actual_timeout} seconds. "
                    "This may be normal for large models - the model will continue downloading "
                    "in the background and will be available when ready."
                )
            raise APIError(f"Failed to initialize provider: {str(e)}")

    def SentenceTransformerEmbedding(
        self,
        model: str = 'BAAI/bge-large-en-v1.5',
        device: Optional[str] = None,
        **kwargs
    ) -> EmbeddingProvider:
        """Convenience method for creating SentenceTransformer embedder"""
        return self.initialize_provider(
            provider_type="sentence_transformers",
            model=model,
            device=device,
            **kwargs
        )

    def get_providers(self) -> List[str]:
        """Get list of available embedding providers"""
        try:
            return self.client.get("/embedding/providers", timeout=self.default_timeout)
        except Exception as e:
            raise APIError(f"Failed to get providers: {str(e)}")

    def call(self, batch: Dict[str, List[Any]], **kwargs) -> Dict[str, List[Any]]:
        """Legacy method - requires explicit provider initialization"""
        raise DeprecationWarning(
            "The global call() method is deprecated. Please initialize a provider first using initialize_provider() or SentenceTransformerEmbedding()"
        )