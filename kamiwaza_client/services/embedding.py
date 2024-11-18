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

    def chunk_text(
        self, 
        text: str, 
        max_length: int = 510, 
        overlap: int = 32,
        preamble_text: str = "",
        return_metadata: bool = False,
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
                params=params
            )
            
            if return_metadata:
                return ChunkResponse(
                    chunks=response["chunks"],
                    offsets=response.get("offsets"),
                    token_counts=response.get("token_counts")
                )
            return response
        except Exception as e:
            raise APIError(f"Operation failed: {str(e)}")

    def embed_chunks(self, text_chunks: List[str], batch_size: int = 64) -> List[List[float]]:
        """Generate embeddings for a list of text chunks."""
        try:
            total_chunks = len(text_chunks)
            logger.info(f"Starting embedding generation for {total_chunks} chunks (batch size: {batch_size})")
            
            result = self._service.client.post(
                "/embedding/batch", 
                params={"batch_size": batch_size, "embedder_id": self.embedder_id},
                json=text_chunks
            )
            
            # Convert embeddings to lists of native Python floats
            result = [[float(x) for x in embedding] for embedding in result]
            
            logger.info(f"Successfully generated embeddings for {total_chunks} chunks")
            return result
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {str(e)}")
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
            response = self._service.client.post(
                "/embedding/generate", 
                json=input_data.model_dump()
            )
            # Convert embedding values to native Python floats
            if 'embedding' in response:
                response['embedding'] = [float(x) for x in response['embedding']]
            
            self._service._model_loaded[self.embedder_id] = True
            return EmbeddingOutput.model_validate(response)
        except Exception as e:
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
                params={"embedder_id": self.embedder_id}
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
        self._model_loaded = {}  # Track which models have been loaded

    def initialize_provider(
        self, 
        provider_type: str, 
        model: str, 
        device: Optional[str] = None,
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
            response = self.client.post(
                "/embedding/initialize", 
                json=config.model_dump()
            )
            provider_id = response["id"]
            self._model_loaded[provider_id] = False  # Track new provider
            return EmbeddingProvider(self, provider_id)
        except Exception as e:
            raise APIError(f"Failed to initialize provider: {str(e)}")

    def HuggingFaceEmbedding(
        self,
        model: str = 'BAAI/bge-large-en-v1.5',
        device: Optional[str] = None,
        **kwargs
    ) -> EmbeddingProvider:
        """Convenience method for creating HuggingFace embedder"""
        return self.initialize_provider(
            provider_type="huggingface_embedding",
            model=model,
            device=device,
            **kwargs
        )

    def get_providers(self) -> List[str]:
        """Get list of available embedding providers"""
        try:
            return self.client.get("/embedding/providers")
        except Exception as e:
            raise APIError(f"Failed to get providers: {str(e)}")

    def call(self, batch: Dict[str, List[Any]], **kwargs) -> Dict[str, List[Any]]:
        """Legacy method - requires explicit provider initialization"""
        raise DeprecationWarning(
            "The global call() method is deprecated. Please initialize a provider first using initialize_provider() or HuggingFaceEmbedding()"
        )