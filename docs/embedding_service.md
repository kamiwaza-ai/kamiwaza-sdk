# Embedding Service

The Embedding Service allows you to create and manage text embeddings.

## Methods

### create_embedding(text: str, model: Any = None, max_length: int = 382, overlap: int = 32, preamble_text: str = "")

Creates an embedding for text.

```python
embedding = client.embedding.create_embedding("This is a sample text", max_length=512)
```

### get_embedding(text: str)

Gets an embedding for text.

```python
embedding = client.embedding.get_embedding("This is a sample text")
```

### chunk_text(text: str, max_length: int = 382, overlap: int = 32, preamble_text: str = "")

Chunks text into smaller pieces.

```python
chunks = client.embedding.chunk_text("This is a long text that needs to be chunked", max_length=100, overlap=10)
```

### embed_chunks(text_chunks: List[str], batch_size: int = 32)

Generates embeddings for text chunks.

```python
embeddings = client.embedding.embed_chunks(["chunk1", "chunk2", "chunk3"], batch_size=16)
```

### reset_model()

Resets the embedding model.

```python
client.embedding.reset_model()
```

### call(batch: List[str], model_name: str = None)

Generates embeddings for a batch of inputs.

```python
embeddings = client.embedding.call(["text1", "text2", "text3"], model_name="my_embedding_model")
```
