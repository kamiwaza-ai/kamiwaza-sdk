# kamiwaza_client/services/retrieval.py

from typing import List
from ..schemas.retrieval import (
    RetrieveRelevantChunksRequest,
    RetrieveRelevantChunksResponse,
    ChunkData
)
from .base_service import BaseService
from ..exceptions import APIError

class RetrievalService(BaseService):
    def retrieve_relevant_chunks(self, request: RetrieveRelevantChunksRequest) -> RetrieveRelevantChunksResponse:
        try:
            response = self.client.post(
                "/retrieval/relevant_chunks",
                json=request.model_dump()
            )
            # Deserialize response into ChunkData objects
            chunks = [ChunkData.model_validate(chunk) for chunk in response.get('chunks', [])]
            return RetrieveRelevantChunksResponse(chunks=chunks)
        except Exception as e:
            raise APIError(f"Failed to retrieve relevant chunks: {str(e)}")
