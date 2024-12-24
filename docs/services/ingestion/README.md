# Ingestion Service

## Overview
The Ingestion Service (`IngestionService`) provides comprehensive data ingestion functionality for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/ingestion.py`, this service handles data ingestion workflows, dataset processing, and document handling with embedding capabilities.

## Key Features
- Data Ingestion
- Dataset Catalog Integration
- Document Processing
- Embedding Generation
- Batch Processing Support

## Data Ingestion

### Available Methods
- `ingest(data: Union[str, List[str], Dict[str, Any]], **kwargs) -> IngestionResponse`: Ingest data
- `ingest_dataset(dataset: Dataset, **kwargs) -> DatasetIngestionResponse`: Ingest dataset to catalog
- `initialize_embedder(provider: str = "default", **kwargs) -> None`: Initialize embedding provider
- `process_documents(documents: List[Document], **kwargs) -> ProcessingResponse`: Process documents

```python
# Simple data ingestion
response = client.ingestion.ingest(
    data="Sample text data",
    chunk_size=512
)

# Dataset ingestion
response = client.ingestion.ingest_dataset(
    dataset=dataset_obj,
    embedding_config={
        "provider": "huggingface",
        "model": "sentence-transformers/all-mpnet-base-v2"
    }
)

# Initialize embedder
client.ingestion.initialize_embedder(
    provider="huggingface",
    model_name="sentence-transformers/all-mpnet-base-v2"
)

# Process documents
response = client.ingestion.process_documents(
    documents=[
        Document(text="doc1", metadata={"source": "file1"}),
        Document(text="doc2", metadata={"source": "file2"})
    ],
    chunk_size=512,
    overlap=50
)
```

## Integration with Other Services
The Ingestion Service works in conjunction with:
1. Embedding Service
   - For generating embeddings of ingested text
2. VectorDB Service
   - For storing processed vectors
3. Catalog Service
   - For dataset management
4. Retrieval Service
   - For accessing processed documents

## Error Handling
The service includes built-in error handling for common scenarios:
```python
try:
    response = client.ingestion.ingest(data)
except EmbeddingError:
    print("Embedding generation failed")
except VectorDBError:
    print("Vector storage failed")
except ProcessingError as e:
    print(f"Document processing failed: {e}")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Initialize embedder before ingestion
2. Use appropriate chunk sizes
3. Include relevant metadata
4. Process documents in batches
5. Monitor ingestion progress
6. Handle errors appropriately
7. Clean up failed ingestions
8. Validate data before ingestion

## Performance Considerations
- Batch size affects processing speed
- Embedding generation time
- Vector database insertion overhead
- Memory usage during processing
- Network bandwidth for large datasets

## Data Formats
The service supports various input formats:
1. Raw Text
   - Single strings
   - Lists of strings
2. Structured Data
   - JSON objects
   - Dictionaries
3. Documents
   - Custom Document objects
   - Metadata support
4. Datasets
   - Catalog integration
   - Batch processing
