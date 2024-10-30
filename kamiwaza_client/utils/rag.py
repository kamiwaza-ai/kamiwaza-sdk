# kamiwaza_client/utils/rag.py

from typing import List, Optional, Dict, Any, Union
from pathlib import Path

class RAGUtils:
    """Helper methods for RAG operations using existing Kamiwaza services."""
    
    def __init__(self, client):
        self.client = client

    def create_rag_collection(
        self,
        collection_name: str,
        embedding_model: Optional[str] = None,
        dims: int = 768
    ) -> Dict[str, Any]:
        """
        Create a collection for RAG with recommended settings.
        Wraps VectorService.add_schema with RAG-optimized defaults.
        """
        # Drop if exists
        try:
            self.client.vectors.drop_schema(collection_name)
        except:
            pass
            
        # Create schema with RAG-optimized settings
        return self.client.vectors.add_schema({
            "collection_name": collection_name,
            "dims": dims,
            "index_type": "IVF_FLAT",
            "metric_type": "IP",
            "auto_fields": True  # Ensures model_name, source, offset tracking
        })

    def process_and_embed_document(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: int = 512,
        chunk_overlap: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Process a document and create embeddings with metadata.
        """
        # Chunk text
        chunks = self.client.embedding.chunk_text(
            text=text, 
            max_length=chunk_size,
            overlap=chunk_overlap
        )
        
        # Get embeddings
        embeddings = self.client.embedding.embed_chunks(chunks)
        
        # Combine with metadata
        results = []
        for chunk, embedding in zip(chunks, embeddings):
            result = {
                "text": chunk,
                "embedding": embedding,
                "metadata": metadata or {}
            }
            results.append(result)
            
        return results

    async def process_and_embed_dataset(
        self,
        documents: Union[str, Path, List[Union[str, Path]]],
        collection_name: str,
        recursive: bool = True,
        batch_size: int = 100
    ) -> Dict[str, Any]:
        """
        Process a dataset of documents and add to vector store.
        """
        # Add to catalog
        dataset = self.client.catalog.create_dataset(
            dataset_name=str(documents),
            platform='file',
            additional_properties={'collection': collection_name}
        )
        
        # Process documents using file runner
        file_runner = self.client.file_runner
        processed = await file_runner.process_files(
            documents,
            batch_size=batch_size,
            recursive=recursive
        )
        
        # Create embeddings and store
        total_chunks = 0
        for batch in processed:
            embedded = self.process_and_embed_document(
                batch.text,
                metadata={"source": batch.source}
            )
            
            # Store in vector DB
            for item in embedded:
                self.client.vectors.insert_vector({
                    "collection_name": collection_name,
                    "vector": item["embedding"],
                    "metadata": {
                        "text": item["text"],
                        **item["metadata"]
                    }
                })
            total_chunks += len(embedded)
            
        return {
            "dataset_id": dataset.id,
            "total_documents": len(processed),
            "total_chunks": total_chunks,
            "collection_name": collection_name
        }

    def semantic_search(
        self,
        query: str,
        collection_name: str,
        limit: int = 5,
        score_threshold: Optional[float] = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Perform semantic search using embeddings.
        """
        # Get query embedding
        query_embedding = self.client.embedding.create_embedding(query).embedding
        
        # Search vectors
        results = self.client.vectors.search_vector({
            "collection_name": collection_name,
            "vector": query_embedding,
            "limit": limit,
            "output_fields": ["text", "source", "score"]
        })
        
        # Filter by score if threshold provided
        if score_threshold is not None:
            results = [r for r in results if r["score"] >= score_threshold]
            
        return results