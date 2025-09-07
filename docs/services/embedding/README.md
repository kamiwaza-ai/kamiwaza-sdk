# Embedding Service

## Overview
The Embedding Service (`EmbeddingService`) provides comprehensive text embedding functionality for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/embedding.py`, this service uses a provider-based architecture for text chunking and embedding generation.

## Key Features
- Provider-Based Architecture
- Text Chunking with Metadata
- Embedding Generation
- Batch Processing
- Multiple Provider Support
- Stateless Design

## Getting Started

The embedding service requires initializing a provider before use:

```python
from kamiwaza_client import KamiwazaClient

client = KamiwazaClient(api_key="your-key")

# Get an embedder instance (required first step)
embedder = client.embedding.get_embedder(
    model="nomic-ai/nomic-embed-text-v1.5",  # optional, this is default
    provider_type="sentencetransformers",     # optional, this is default  
    device="cuda"                             # optional, auto-detect if None
)
```

## Text Processing

### Available Methods
- `chunk_text(text: str, max_length: int = 1024, overlap: int = 102, preamble_text: str = "", return_metadata: bool = False) -> Union[List[str], ChunkResponse]`: Split text into chunks
- `embed_chunks(text_chunks: List[str], batch_size: int = 64) -> List[List[float]]`: Generate embeddings for chunks
- `create_embedding(text: str, max_length: int = 1024, overlap: int = 102, preamble_text: str = "") -> EmbeddingOutput`: Create embedding for text
- `get_embedding(text: str, return_offset: bool = False) -> EmbeddingOutput`: Generate embedding for text

```python
# Initialize embedder first
embedder = client.embedding.get_embedder()

# Split text into chunks
chunks = embedder.chunk_text(
    text="Long document text...",
    max_length=1024,
    overlap=102
)

# Or get chunks with metadata
chunk_response = embedder.chunk_text(
    text="Long document text...",
    max_length=1024,
    overlap=102,
    return_metadata=True
)
# Access: chunk_response.chunks, chunk_response.offsets, chunk_response.token_counts

# Generate embeddings for chunks
embeddings = embedder.embed_chunks(chunks, batch_size=64)

# Create single embedding
result = embedder.create_embedding("Sample text")
embedding_vector = result.embedding  # List[float]

# Generate embedding (alternative method)
result = embedder.get_embedding("Sample text")
embedding_vector = result.embedding
```

## Provider Management

### Getting an Embedder
The primary method for working with embeddings is through `get_embedder()`:

```python
# Get embedder with default settings
embedder = client.embedding.get_embedder()

# Get embedder with custom model
embedder = client.embedding.get_embedder(
    model="sentence-transformers/all-mpnet-base-v2",
    provider_type="sentencetransformers",
    device="cuda"  # or "cpu", "mps", None for auto-detect
)

# List available providers
providers = client.embedding.get_providers()
```

### Default Configuration
- **Default Model**: `nomic-ai/nomic-embed-text-v1.5`
- **Default Provider**: `sentencetransformers`
- **Default Device**: Auto-detected based on availability

## Return Types

The service returns Pydantic models for structured data:

### EmbeddingOutput
```python
class EmbeddingOutput:
    embedding: List[float]  # The embedding vector
    offset: Optional[int]   # Offset in original text (if requested)
```

### ChunkResponse
```python
class ChunkResponse:
    chunks: List[str]                    # Text chunks
    offsets: Optional[List[int]]         # Start positions in original text
    token_counts: Optional[List[int]]    # Token count per chunk
    metadata: Optional[List[dict]]       # Additional metadata per chunk
```

## Error Handling
The service uses a unified error handling approach:
```python
from kamiwaza_client.exceptions import APIError

try:
    embedder = client.embedding.get_embedder()
    result = embedder.create_embedding("text")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Deprecated Methods

The following methods are deprecated and should not be used:

### HuggingFaceEmbedding()
- **Status**: Deprecated
- **Replacement**: Use `get_embedder()` instead
- **Warning**: Logs deprecation warning when called

### reset_model()
- **Status**: Deprecated (no-op in stateless design)
- **Returns**: `{"status": "no-op"}`
- **Note**: Model state is now handled per request

### call()
- **Status**: Deprecated
- **Replacement**: Use `get_embedder()` then call methods on the provider
- **Raises**: `DeprecationWarning`

## Best Practices

1. **Always initialize an embedder first** using `get_embedder()`
2. **Choose appropriate chunk sizes** based on your model's context window
3. **Use batch processing** for multiple texts to improve performance
4. **Handle overlaps properly** to maintain context between chunks
5. **Consider memory usage** when processing large batches
6. **Cache embeddings** when possible to avoid recomputation
7. **Use return_metadata=True** when you need chunk offsets or token counts

## Complete Example

```python
from kamiwaza_client import KamiwazaClient
from kamiwaza_client.exceptions import APIError

# Initialize client
client = KamiwazaClient(api_key="your-key")

# Get embedder
embedder = client.embedding.get_embedder(
    model="nomic-ai/nomic-embed-text-v1.5",
    provider_type="sentencetransformers"
)

try:
    # Process a document
    document = "Your long document text here..."
    
    # Chunk with metadata
    chunk_response = embedder.chunk_text(
        text=document,
        max_length=512,
        overlap=50,
        return_metadata=True
    )
    
    # Generate embeddings
    embeddings = embedder.embed_chunks(
        chunk_response.chunks,
        batch_size=32
    )
    
    # Process results
    for i, (chunk, embedding) in enumerate(zip(chunk_response.chunks, embeddings)):
        print(f"Chunk {i}: {len(embedding)} dimensions")
        if chunk_response.offsets:
            print(f"  Offset: {chunk_response.offsets[i]}")
        if chunk_response.token_counts:
            print(f"  Tokens: {chunk_response.token_counts[i]}")
            
except APIError as e:
    print(f"Embedding operation failed: {e}")
```
