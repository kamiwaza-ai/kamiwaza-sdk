# VectorDB Service

## Overview
The VectorDB Service (`VectorDBService`) provides comprehensive vector database management functionality for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/vectordb.py`, this service handles vector storage, retrieval, and similarity search operations.

## Key Features
- Vector Database Management
- Vector Storage and Retrieval
- Similarity Search
- Simplified Vector Operations
- Database Lifecycle Management

## Vector Database Management

### Available Methods
- `create_vectordb(config: CreateVectorDB) -> VectorDB`: Create new vector database
- `get_vectordbs() -> List[VectorDB]`: List all vector databases
- `get_vectordb(vectordb_id: UUID) -> VectorDB`: Get database details
- `remove_vectordb(vectordb_id: UUID)`: Remove vector database

```python
# Create vector database
vectordb = client.vectordb.create_vectordb(CreateVectorDB(
    name="my-vectors",
    dimension=768,
    metric="cosine"
))

# List databases
databases = client.vectordb.get_vectordbs()

# Get specific database
db = client.vectordb.get_vectordb(vectordb_id)

# Remove database
client.vectordb.remove_vectordb(vectordb_id)
```

## Vector Operations

### Available Methods
- `insert_vectors(vectordb_id: UUID, vectors: List[Vector]) -> InsertResponse`: Insert vectors
- `search_vectors(vectordb_id: UUID, query: List[float], k: int = 10) -> List[SearchResult]`: Search vectors
- `insert(vectordb_id: UUID, data: Dict[str, Any]) -> InsertResponse`: Simplified vector insertion
- `search(vectordb_id: UUID, query: str, k: int = 10) -> List[SearchResult]`: Simplified vector search

```python
# Insert vectors
response = client.vectordb.insert_vectors(
    vectordb_id=db_id,
    vectors=[
        Vector(id="vec1", vector=[0.1, 0.2, 0.3], metadata={"text": "example"})
    ]
)

# Search vectors
results = client.vectordb.search_vectors(
    vectordb_id=db_id,
    query=[0.1, 0.2, 0.3],
    k=5
)

# Simplified operations
# Insert with automatic vector generation
response = client.vectordb.insert(
    vectordb_id=db_id,
    data={"text": "example text", "metadata": {"source": "doc1"}}
)

# Search with automatic query vector generation
results = client.vectordb.search(
    vectordb_id=db_id,
    query="example query",
    k=5
)
```

## Error Handling
The service includes built-in error handling for common scenarios:
```python
try:
    vectordb = client.vectordb.create_vectordb(config)
except DimensionError as e:
    print(f"Invalid dimension: {e}")
except MetricError as e:
    print(f"Invalid metric: {e}")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Choose appropriate vector dimensions based on your embedding model
2. Select the right similarity metric for your use case
3. Use batch operations for better performance
4. Include relevant metadata with vectors
5. Clean up unused databases
6. Use simplified operations when working with text data
7. Monitor database size and performance
8. Implement proper error handling for vector operations
