# Embedding Service

## Overview
The Embedding Service (`EmbeddingService`) provides comprehensive text embedding functionality for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/embedding.py`, this service handles text chunking, embedding generation, and provider management.

## Key Features
- Text Chunking
- Embedding Generation
- Multiple Provider Support
- Batch Processing
- Model Management
- Provider Initialization

## Text Processing

### Available Methods
- `chunk_text(text: str, chunk_size: int = 512) -> List[str]`: Split text into chunks
- `embed_chunks(chunks: List[str]) -> List[List[float]]`: Generate embeddings for chunks
- `create_embedding(text: str) -> List[float]`: Create embedding for text
- `get_embedding(embedding_id: UUID) -> Embedding`: Get existing embedding

```python
# Split text into chunks
chunks = client.embedding.chunk_text(
    text="Long document text...",
    chunk_size=512
)

# Generate embeddings for chunks
embeddings = client.embedding.embed_chunks(chunks)

# Create single embedding
embedding = client.embedding.create_embedding("Sample text")

# Retrieve existing embedding
stored_embedding = client.embedding.get_embedding(embedding_id)
```

## Model Management

### Available Methods
- `reset_model()`: Reset embedding model
- `call(texts: List[str]) -> List[List[float]]`: Generate batch embeddings
- `initialize_provider(provider: str, **kwargs)`: Initialize embedding provider
- `HuggingFaceEmbedding(model_name: str)`: Create HuggingFace embedder
- `get_providers() -> List[str]`: List available providers

```python
# Reset model
client.embedding.reset_model()

# Batch embedding generation
embeddings = client.embedding.call(["text1", "text2", "text3"])

# Initialize provider
client.embedding.initialize_provider(
    provider="huggingface",
    model_name="sentence-transformers/all-mpnet-base-v2"
)

# Create HuggingFace embedder
embedder = client.embedding.HuggingFaceEmbedding(
    model_name="sentence-transformers/all-mpnet-base-v2"
)

# List available providers
providers = client.embedding.get_providers()
```

## Error Handling
The service includes built-in error handling for common scenarios:
```python
try:
    embedding = client.embedding.create_embedding("text")
except ModelNotFoundError:
    print("Embedding model not found")
except ProviderError as e:
    print(f"Provider error: {e}")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Choose appropriate chunk sizes for your use case
2. Use batch processing for better performance
3. Initialize providers with appropriate models
4. Handle model resets properly
5. Monitor embedding quality
6. Use appropriate error handling
7. Consider memory usage for large batches
8. Cache frequently used embeddings

## Provider Configuration
The service supports multiple embedding providers:
1. HuggingFace
   - Supports various model architectures
   - Customizable model selection
   - Local and remote inference
2. Custom Providers
   - Extensible provider interface
   - Custom model integration
   - Provider-specific configurations
