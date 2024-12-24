# Retrieval Service

## Overview
The Retrieval Service (`RetrievalService`) provides text chunk retrieval functionality for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/retrieval.py`, this service handles the retrieval of relevant text chunks based on queries, enabling efficient information retrieval.

## Key Features
- Relevant Text Chunk Retrieval
- Query-based Search
- Integration with Vector Database
- Semantic Search Capabilities

## Text Chunk Retrieval

### Available Methods
- `retrieve_relevant_chunks(query: str, k: int = 5) -> List[TextChunk]`: Get relevant text chunks based on query

```python
# Retrieve relevant chunks
chunks = client.retrieval.retrieve_relevant_chunks(
    query="What is machine learning?",
    k=5  # Number of chunks to retrieve
)

# Process retrieved chunks
for chunk in chunks:
    print(f"Text: {chunk.text}")
    print(f"Score: {chunk.score}")
    print(f"Source: {chunk.metadata.get('source')}")
```

## Integration with Other Services
The Retrieval Service works in conjunction with:
1. Embedding Service
   - For converting queries into vector representations
2. VectorDB Service
   - For performing similarity search
3. Ingestion Service
   - For accessing processed and stored text chunks

## Error Handling
The service includes built-in error handling for common scenarios:
```python
try:
    chunks = client.retrieval.retrieve_relevant_chunks(
        query="example query"
    )
except VectorDBError:
    print("Vector database error")
except EmbeddingError:
    print("Embedding generation error")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Use specific and focused queries
2. Adjust the number of chunks (k) based on your needs
3. Consider chunk relevance scores
4. Process chunks in order of relevance
5. Handle empty result sets appropriately
6. Implement proper error handling
7. Consider caching frequently retrieved chunks
8. Monitor retrieval performance

## Performance Considerations
- Query length affects retrieval time
- Number of chunks (k) impacts response time
- Vector database size influences search speed
- Embedding generation adds processing overhead
