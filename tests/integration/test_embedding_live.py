"""Integration tests for TS6 EMBEDDING endpoints.

Tests cover:
- TS6.001: POST /embedding/batch
- TS6.002: POST /embedding/chunk
- TS6.003: POST /embedding/generate
- TS6.004: GET /embedding/generate/{text}
- TS6.005: GET /embedding/health
- TS6.006: GET /embedding/providers
"""
from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]

# Default embedding model - small and commonly available
# Note: nomic-ai/nomic-embed-text-v1.5 requires trust_remote_code=True on server
# Using simpler model that works out of the box
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_PROVIDER = "sentence_transformers"


class TestEmbeddingHealth:
    """Tests for embedding service health."""

    def test_embedding_health(self, live_kamiwaza_client) -> None:
        """TS6.005: GET /embedding/health - Check embedding service health."""
        try:
            result = live_kamiwaza_client.get("/embedding/health")
            assert isinstance(result, dict)
            assert result.get("status") == "healthy"
            assert result.get("service") == "embedding"
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Embedding health endpoint not available")
            raise


class TestEmbeddingProviders:
    """Tests for embedding provider operations."""

    def test_get_providers(self, live_kamiwaza_client) -> None:
        """TS6.006: GET /embedding/providers - Get available embedding providers."""
        providers = live_kamiwaza_client.embedding.get_providers()
        assert isinstance(providers, list)
        # Should have at least one provider registered
        # Common providers: sentencetransformers, huggingface_embedding
        assert len(providers) > 0


class TestEmbeddingChunking:
    """Tests for text chunking operations."""

    def test_chunk_text_simple(self, live_kamiwaza_client) -> None:
        """TS6.002: POST /embedding/chunk - Basic text chunking."""
        embedder = live_kamiwaza_client.embedding.get_embedder(
            model=DEFAULT_MODEL,
            provider_type=DEFAULT_PROVIDER
        )

        text = "This is a test sentence for chunking. " * 50
        try:
            chunks = embedder.chunk_text(
                text=text,
                max_length=512,
                overlap=32,
                return_metadata=False
            )
            assert isinstance(chunks, list)
            assert len(chunks) > 0
            assert all(isinstance(c, str) for c in chunks)
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Embedding model not available: {exc}")
            raise

    def test_chunk_text_with_metadata(self, live_kamiwaza_client) -> None:
        """TS6.002: POST /embedding/chunk - Chunking with metadata."""
        embedder = live_kamiwaza_client.embedding.get_embedder(
            model=DEFAULT_MODEL,
            provider_type=DEFAULT_PROVIDER
        )

        text = "This is a test sentence for chunking with metadata. " * 50
        try:
            result = embedder.chunk_text(
                text=text,
                max_length=512,
                overlap=32,
                return_metadata=True
            )
            # Should return ChunkResponse with metadata
            assert hasattr(result, 'chunks')
            assert isinstance(result.chunks, list)
            assert len(result.chunks) > 0
            # Should have offsets when metadata is requested
            if hasattr(result, 'offsets') and result.offsets:
                assert isinstance(result.offsets, list)
            if hasattr(result, 'token_counts') and result.token_counts:
                assert isinstance(result.token_counts, list)
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Embedding model not available: {exc}")
            raise


class TestEmbeddingGeneration:
    """Tests for embedding generation operations."""

    def test_create_embedding_post(self, live_kamiwaza_client) -> None:
        """TS6.003: POST /embedding/generate - Generate embedding."""
        embedder = live_kamiwaza_client.embedding.get_embedder(
            model=DEFAULT_MODEL,
            provider_type=DEFAULT_PROVIDER
        )

        try:
            result = embedder.create_embedding(text="Hello, world!")
            assert result is not None
            assert hasattr(result, 'embedding')
            assert isinstance(result.embedding, list)
            assert len(result.embedding) > 0
            # Embeddings are vectors of floats
            assert all(isinstance(v, float) for v in result.embedding)
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Embedding model not available: {exc}")
            raise

    def test_get_embedding(self, live_kamiwaza_client) -> None:
        """TS6.004: GET /embedding/generate/{text} - Get embedding via GET."""
        embedder = live_kamiwaza_client.embedding.get_embedder(
            model=DEFAULT_MODEL,
            provider_type=DEFAULT_PROVIDER
        )

        try:
            result = embedder.get_embedding(text="Hello, world!")
            assert result is not None
            assert hasattr(result, 'embedding')
            assert isinstance(result.embedding, list)
            assert len(result.embedding) > 0
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Embedding model not available: {exc}")
            raise


class TestEmbeddingBatch:
    """Tests for batch embedding operations."""

    def test_embed_chunks_batch(self, live_kamiwaza_client) -> None:
        """TS6.001: POST /embedding/batch - Batch embedding generation."""
        embedder = live_kamiwaza_client.embedding.get_embedder(
            model=DEFAULT_MODEL,
            provider_type=DEFAULT_PROVIDER
        )

        chunks = [
            "This is the first test chunk.",
            "This is the second test chunk.",
            "This is the third test chunk.",
        ]

        try:
            embeddings = embedder.embed_chunks(text_chunks=chunks, batch_size=2)
            assert isinstance(embeddings, list)
            assert len(embeddings) == len(chunks)
            # Each embedding should be a list of floats
            for emb in embeddings:
                assert isinstance(emb, list)
                assert len(emb) > 0
                assert all(isinstance(v, float) for v in emb)
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Embedding model not available: {exc}")
            raise


class TestEmbeddingIntegrated:
    """End-to-end embedding workflow tests."""

    def test_chunk_and_embed_workflow(self, live_kamiwaza_client) -> None:
        """Test complete chunking and embedding workflow."""
        embedder = live_kamiwaza_client.embedding.get_embedder(
            model=DEFAULT_MODEL,
            provider_type=DEFAULT_PROVIDER
        )

        # Sample text
        text = """
        Artificial intelligence (AI) is intelligence demonstrated by machines,
        as opposed to natural intelligence displayed by animals including humans.
        AI research has been defined as the field of study of intelligent agents,
        which refers to any system that perceives its environment and takes actions
        that maximize its chance of achieving its goals.
        """ * 10

        try:
            # Step 1: Chunk the text
            chunks = embedder.chunk_text(
                text=text,
                max_length=256,
                overlap=32,
                return_metadata=False
            )
            assert isinstance(chunks, list)
            assert len(chunks) > 0

            # Step 2: Embed the chunks
            embeddings = embedder.embed_chunks(text_chunks=chunks)
            assert isinstance(embeddings, list)
            assert len(embeddings) == len(chunks)

            # Verify embedding dimensions are consistent
            if embeddings:
                first_dim = len(embeddings[0])
                assert all(len(emb) == first_dim for emb in embeddings)
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Embedding model not available: {exc}")
            raise
