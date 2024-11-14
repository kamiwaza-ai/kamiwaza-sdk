# kamiwaza_client/schemas/ingestion.py
from pydantic import BaseModel
from typing import Optional

class IngestionConfig(BaseModel):
    dataset_path: str
    platform: str = 'file'
    recursive: bool = True
    env: str = 'PROD'
    location: str = 'MAIN'
    description: str = ''
    collection_name: str = ''
    embedder_provider_type: str = 'huggingface_embedding'
    embedder_model: str = 'BAAI/bge-large-en-v1.5'
    max_length: int = 500
    overlap: int = 50
    batch_size: int = 32
    max_files: Optional[int] = None  


