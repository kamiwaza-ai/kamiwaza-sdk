# kamiwaza_client/services/ingestion.py

from typing import List, Optional
from ..schemas.ingestion import IngestionConfig
from .base_service import BaseService
from .catalog import CatalogService
from .embedding import EmbeddingService
from .vectordb import VectorDBService
import os
import glob

class IngestionService(BaseService):
    def __init__(self, client):
        super().__init__(client)
        # Initialize services
        self.catalog_service = CatalogService(client)
        self.embedding_service = EmbeddingService(client)
        self.vectordb_service = VectorDBService(client)

        # Initialize state variables
        self.catalog_urn = None
        self.embedder = None
        self.config = None

    def ingest(self, config: IngestionConfig):
            self.config = config
            # Ensure dataset_path is absolute
            self.config.dataset_path = os.path.abspath(self.config.dataset_path)
            # Set collection name if not provided
            if not self.config.collection_name:
                self.config.collection_name = os.path.basename(os.path.normpath(self.config.dataset_path))

            # Step 1: Ingest dataset into catalog
            self.ingest_dataset()

            # Step 2: Initialize embedder
            self.initialize_embedder()

            # Step 3: Process documents
            self.process_documents()

    def ingest_dataset(self):
        dataset_name = self.config.dataset_path  # Use absolute path

        # Try to retrieve the dataset first
        datasets = self.catalog_service.list_datasets()
        dataset_exists = False
        for dataset in datasets:
            if dataset.id == dataset_name:
                self.catalog_urn = dataset.urn
                dataset_exists = True
                break
        
        if not dataset_exists:
            # Create the dataset if it doesn't exist
            dataset = self.catalog_service.create_dataset(
                dataset_name=dataset_name,
                platform=self.config.platform,
                environment=self.config.env,
                description=self.config.description,
                location=self.config.location
            )
            self.catalog_urn = dataset.urn
        else:
            # If dataset exists, update it with new data
            self.catalog_service.ingest_by_path(
                path=dataset_name,
                platform=self.config.platform,
                recursive=self.config.recursive,
                env=self.config.env,
                location=self.config.location,
                description=self.config.description
            )

        if not self.catalog_urn:
            raise ValueError(f"Failed to create or retrieve dataset with path {self.config.dataset_path}")

    def initialize_embedder(self):
        # Initialize embedding provider
        self.embedder = self.embedding_service.initialize_provider(
            provider_type=self.config.embedder_provider_type,
            model=self.config.embedder_model
        )

    def process_documents(self):
        # Get list of text files
        text_files = glob.glob(os.path.join(self.config.dataset_path, '**/*.md'), recursive=self.config.recursive)
        
        # Limit number of files if max_files is set
        if self.config.max_files:
            text_files = text_files[:self.config.max_files]
            print(f"Processing {len(text_files)} files (limited by max_files={self.config.max_files})")
        else:
            print(f"Processing all {len(text_files)} files found")

        all_chunks = []
        metadata_list = []
        failed_files = []

        # Process files and collect chunks
        for i, file_path in enumerate(text_files, 1):
            try:
                print(f"Processing file {i}/{len(text_files)}: {os.path.basename(file_path)}")
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                chunk_response = self.embedder.chunk_text(
                    text=text,
                    max_length=self.config.max_length,
                    overlap=self.config.overlap,
                    preamble_text="",
                    return_metadata=True
                )
                chunks = chunk_response.chunks
                offsets = chunk_response.offsets

                all_chunks.extend(chunks)
                for offset in offsets:
                    metadata = {
                        'source': file_path,
                        'offset': offset,
                        'catalog_urn': self.catalog_urn
                    }
                    metadata_list.append(metadata)
            except Exception as e:
                failed_files.append(file_path)
                print(f"Error processing {file_path}: {str(e)}")
                continue

        print(f"\nFinished processing files:")
        print(f"Total files processed: {len(text_files)}")
        print(f"Total chunks created: {len(all_chunks)}")
        print(f"Total metadata entries: {len(metadata_list)}")
        print(f"Failed files: {len(failed_files)}")

        # Process in batches of 1000 vectors
        BATCH_SIZE = 1000
        total_processed = 0
        
        for i in range(0, len(all_chunks), BATCH_SIZE):
            batch_end = min(i + BATCH_SIZE, len(all_chunks))
            chunk_batch = all_chunks[i:batch_end]
            metadata_batch = metadata_list[i:batch_end]

            print(f"\nProcessing vector batch {i//BATCH_SIZE + 1}/{(len(all_chunks) + BATCH_SIZE - 1)//BATCH_SIZE}")
            
            # Generate embeddings for batch
            embeddings_batch = self.embedder.embed_chunks(
                text_chunks=chunk_batch,
                batch_size=self.config.batch_size
            )

            # Insert batch into vector database
            self.vectordb_service.insert(
                vectors=embeddings_batch,
                metadata=metadata_batch,
                collection_name=self.config.collection_name
            )
            
            total_processed += len(chunk_batch)
            print(f"Processed {total_processed}/{len(all_chunks)} vectors")

        print("\nCompleted vector insertion")