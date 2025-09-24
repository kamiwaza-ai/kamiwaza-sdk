# kamiwaza_sdk/services/embedding.py

from typing import List, Optional, Any, Dict, Union
from ..schemas.embedding import EmbeddingOutput, ChunkResponse
from .base_service import BaseService
from ..exceptions import APIError
import logging

logger = logging.getLogger(__name__)

class EmbeddingProvider:
    """Provider class for handling embedder-specific operations"""
    
    def __init__(self, service: 'EmbeddingService', model: str, provider_type: str, device: Optional[str] = None):
        self._service = service
        self.model = model
        self.provider_type = provider_type
        self.device = device

    def chunk_text(
        self, 
        text: str, 
        max_length: int = 1024, 
        overlap: int = 102,
        preamble_text: str = "",
        return_metadata: bool = False,
    ) -> Union[List[str], ChunkResponse]:
        """Chunk text into smaller pieces."""
        # Parameter validation
        if max_length < 100:
            max_length = 1024
        if overlap >= max_length // 2:
            overlap = max_length // 10
            
        params = {
            "model": self.model,
            "provider_type": self.provider_type,
        }
        if self.device:
            params["device"] = self.device
        
        body = {
            "text": text,
            "max_length": max_length,
            "overlap": overlap,
            "preamble_text": preamble_text,
            "return_metadata": return_metadata
        }
        
        try:
            response = self._service.client.post(
                "/embedding/chunk", 
                params=params,
                json=body
            )
            
            if return_metadata:
                return ChunkResponse(
                    chunks=response["chunks"],
                    offsets=response.get("offsets"),
                    token_counts=response.get("token_counts"),
                    metadata=response.get("metadata", [])
                )
            return response
        except Exception as e:
            raise APIError(f"Operation failed: {str(e)}")

    def embed_chunks(self, text_chunks: List[str], batch_size: int = 64) -> List[List[float]]:
        """Generate embeddings for a list of text chunks."""
        try:
            total_chunks = len(text_chunks)
            logger.info(f"Starting embedding generation for {total_chunks} chunks (batch size: {batch_size})")
            
            params = {
                "model": self.model,
                "provider_type": self.provider_type,
                "batch_size": batch_size
            }
            if self.device:
                params["device"] = self.device
            
            result = self._service.client.post(
                "/embedding/batch", 
                params=params,
                json=text_chunks
            )
            
            # Convert embeddings to lists of native Python floats
            result = [[float(x) for x in embedding] for embedding in result]
            
            logger.info(f"Successfully generated embeddings for {total_chunks} chunks")
            return result
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {str(e)}")
            raise APIError(f"Operation failed: {str(e)}")

    def create_embedding(self, text: str, max_length: int = 1024,
                        overlap: int = 102, preamble_text: str = "") -> EmbeddingOutput:
        """Create an embedding for the given text."""
        request_data = {
            "text": text,
            "model": self.model,
            "provider_type": self.provider_type
        }
        if self.device:
            request_data["device"] = self.device
            
        try:
            response = self._service.client.post(
                "/embedding/generate", 
                json=request_data
            )
            # Convert embedding values to native Python floats
            if 'embedding' in response:
                response['embedding'] = [float(x) for x in response['embedding']]
            
            return EmbeddingOutput.model_validate(response)
        except Exception as e:
            raise APIError(f"Operation failed: {str(e)}")

    def get_embedding(self, text: str, return_offset: bool = False) -> EmbeddingOutput:
        """Get an embedding for the given text."""
        params = {
            "model": self.model,
            "provider_type": self.provider_type,
            "return_offset": return_offset
        }
        if self.device:
            params["device"] = self.device
            
        response = self._service.client.get(
            f"/embedding/generate/{text}",
            params=params
        )
        return EmbeddingOutput.model_validate(response)

    def reset_model(self) -> Dict[str, str]:
        """Reset the embedding model - deprecated in stateless design."""
        logger.warning("reset_model() is deprecated in the stateless design")
        return {"status": "no-op"}

    def call(self, batch: Dict[str, List[Any]], model_name: Optional[str] = None) -> Dict[str, List[Any]]:
        """Generate embeddings for a batch of inputs."""
        raise NotImplementedError("Batch call not yet implemented in stateless design")

class EmbeddingService(BaseService):
    """Main service class for managing embedding operations"""

    def __init__(self, client):
        super().__init__(client)
        self._default_model = 'nomic-ai/nomic-embed-text-v1.5'
        self._default_provider = 'sentencetransformers'

    def get_embedder(
        self,
        model: Optional[str] = None,
        provider_type: Optional[str] = None,
        device: Optional[str] = None,
        **kwargs
    ) -> EmbeddingProvider:
        """Get an embedding provider with the specified or default configuration
        
        Args:
            model: Model name to use, defaults to nomic-ai/nomic-embed-text-v1.5
            provider_type: Provider type, defaults to sentencetransformers
            device: Device to use (cpu, cuda, mps), defaults to None (auto-detect)
            **kwargs: Additional provider-specific arguments
            
        Returns:
            EmbeddingProvider instance
        """
        return EmbeddingProvider(
            self,
            model=model or self._default_model,
            provider_type=provider_type or self._default_provider,
            device=device
        )

    # Deprecated method - left for backward compatibility
    def HuggingFaceEmbedding(
        self,
        model: str = 'nomic-ai/nomic-embed-text-v1.5',
        device: Optional[str] = None,
        **kwargs
    ) -> EmbeddingProvider:
        """Deprecated: Use get_embedder() instead"""
        logger.warning("HuggingFaceEmbedding() is deprecated. Please use get_embedder() instead.")
        return self.get_embedder(
            model=model, 
            provider_type='huggingface_embedding',
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
            "The global call() method is deprecated. Please initialize a provider first using get_embedder()"
        )