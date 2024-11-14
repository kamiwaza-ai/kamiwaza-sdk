# VectorDB Service

The VectorDB Service allows you to interact with vector databases for similarity search and embeddings.

## Methods

### create_vectordb(vectordb_data: CreateVectorDB)

Creates a new vector database instance.

```python
new_vectordb = CreateVectorDB(name="my_vectordb", engine="faiss", dimension=512)
created_vectordb = client.vectordb.create_vectordb(new_vectordb)
```

### get_vectordbs(engine: Optional[str] = None)

Lists all vector databases.

```python
vectordbs = client.vectordb.get_vectordbs(engine="faiss")
```

### get_vectordb(vectordb_id: UUID)

Gets a vector database by ID.

```python
vectordb = client.vectordb.get_vectordb("vectordb_id_here")
```

### remove_vectordb(vectordb: UUID)

Removes a vector database.

```python
client.vectordb.remove_vectordb("vectordb_id_here")
```
