# SDK Architecture and Patterns

## Client-Service Architecture

The SDK follows a lazy-loading service pattern:

```python
# Main client acts as orchestrator
class KamiwazaClient:
    @property
    def models(self) -> ModelsService:
        # Services instantiated only when accessed
        if not hasattr(self, '_models'):
            self._models = ModelsService(self._client)
        return self._models
```

### Key Principles
1. **Lazy Loading**: Services created on first access
2. **Single Instance**: One service instance per client
3. **Shared HTTP Client**: All services share authenticated client
4. **Clean Separation**: Client orchestrates, services implement

## Service Layer Pattern

Every service MUST follow this structure:

```python
from kamiwaza_sdk.services.base_service import BaseService
from kamiwaza_sdk.schemas.models import Model, ModelList

class ModelsService(BaseService):
    """Service for model-related operations."""
    
    def list(self, **kwargs) -> ModelList:
        """Public method returns Pydantic models."""
        response = self._request("GET", "/models", params=kwargs)
        return ModelList(**response)
    
    def get(self, model_id: str) -> Model:
        """Always use domain objects, not raw dicts."""
        response = self._request("GET", f"/models/{model_id}")
        return Model(**response)
```

### Service Rules
1. Inherit from `BaseService`
2. Use `self._request()` for HTTP calls
3. Return Pydantic models, not raw responses
4. Handle service-specific errors
5. Keep business logic in service, not in schemas

## Mixin Pattern for Complex Services

For services with many features, use mixins:

```python
# services/models/base.py
class ModelsService(
    BaseService,
    SearchMixin,
    DownloadMixin,
    FileMixin,
    ConfigMixin
):
    """Composed from focused mixins."""
    pass

# services/models/search.py
class SearchMixin:
    """Search-specific functionality."""
    
    def search(self, query: str, **kwargs) -> ModelSearchResults:
        response = self._request("POST", "/models/search", json={
            "query": query,
            **kwargs
        })
        return ModelSearchResults(**response)
```

### Mixin Guidelines
1. One mixin per feature area
2. Mixins can depend on `BaseService` methods
3. Keep mixins focused and cohesive
4. Document mixin dependencies

## Schema Design Patterns

### Request/Response Models
```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class Model(BaseModel):
    """Single model representation."""
    id: str
    name: str
    family: str
    created_at: datetime
    metadata: Optional[dict] = Field(default_factory=dict)
    
    class Config:
        # Allow field population from API responses
        extra = "allow"

class ModelList(BaseModel):
    """Paginated list response."""
    items: List[Model]
    total: int
    page: int = 1
    per_page: int = 20
```

### Schema Rules
1. Use Pydantic for all API contracts
2. Allow `extra` fields for forward compatibility
3. Provide sensible defaults where appropriate
4. Use `Field()` for validation constraints
5. Keep schemas in dedicated modules

## Authentication Pattern

Authentication is handled centrally:

```python
class AuthenticationManager:
    """Manages all authentication concerns."""
    
    def get_headers(self) -> dict:
        """Returns current auth headers."""
        if self.api_key:
            return {"X-API-Key": self.api_key}
        elif self.token:
            return {"Authorization": f"Bearer {self.token}"}
    
    def handle_auth_error(self, response):
        """Refresh token if needed."""
        if response.status_code == 401 and self.refresh_token:
            self._refresh_access_token()
            return True  # Retry request
        return False
```

### Auth Guidelines
1. Never expose credentials in logs
2. Support both API key and OAuth
3. Auto-refresh OAuth tokens
4. Centralize auth logic
5. Make auth transparent to services

## Error Handling Hierarchy

```python
# exceptions.py
class KamiwazaError(Exception):
    """Base exception for all SDK errors."""
    pass

class KamiwazaAPIError(KamiwazaError):
    """API request failed."""
    pass

class AuthenticationError(KamiwazaAPIError):
    """401/403 responses."""
    pass

class ResourceNotFoundError(KamiwazaAPIError):
    """404 responses."""
    pass

class ValidationError(KamiwazaAPIError):
    """400/422 responses."""
    pass
```

### Error Handling Rules
1. Map HTTP codes to semantic exceptions
2. Preserve original error context
3. Provide helpful error messages
4. Allow errors to bubble up
5. Don't catch and re-wrap unnecessarily

## Progress Tracking Pattern

For long-running operations:

```python
from kamiwaza_sdk.utils.progress import ProgressTracker

def download_model(self, model_id: str, path: Path) -> Path:
    """Download with progress tracking."""
    
    # Get download metadata
    info = self._get_download_info(model_id)
    
    # Create progress tracker
    with ProgressTracker(total=info.size, desc=f"Downloading {info.name}") as tracker:
        # Download in chunks
        for chunk in self._download_chunks(info.url):
            path.write_bytes(chunk)
            tracker.update(len(chunk))
    
    return path
```

### Progress Guidelines
1. Use for operations > 5 seconds
2. Provide meaningful descriptions
3. Update regularly but not too frequently
4. Support both TTY and non-TTY environments
5. Make progress tracking optional

## Configuration Pattern

```python
from pydantic import BaseSettings

class SDKConfig(BaseSettings):
    """SDK-wide configuration."""
    
    api_key: Optional[str] = None
    base_url: str = "https://api.kamiwaza.ai"
    timeout: int = 30
    max_retries: int = 3
    
    class Config:
        env_prefix = "KAMIWAZA_"  # KAMIWAZA_API_KEY, etc.
```

### Config Rules
1. Use Pydantic Settings for validation
2. Support environment variables
3. Provide sensible defaults
4. Document all settings
5. Keep config immutable after init

## Testing Patterns

### Service Testing
```python
import pytest
from unittest.mock import Mock, patch

class TestModelsService:
    @pytest.fixture
    def service(self):
        client = Mock()
        return ModelsService(client)
    
    @patch('requests.request')
    def test_list_models(self, mock_request, service):
        # Mock response
        mock_request.return_value.json.return_value = {
            "items": [{"id": "1", "name": "model1"}],
            "total": 1
        }
        
        # Test
        result = service.list()
        
        # Verify
        assert len(result.items) == 1
        assert result.items[0].name == "model1"
```

### Testing Guidelines
1. Mock HTTP calls, don't hit real API
2. Test both success and error paths
3. Verify request construction
4. Test schema validation
5. Keep tests fast and isolated

## OpenAI Compatibility Layer

Special pattern for drop-in compatibility:

```python
class OpenAIService:
    """OpenAI-compatible interface."""
    
    @property
    def chat(self):
        return ChatCompletions(self._client)
    
    @property  
    def completions(self):
        return Completions(self._client)

# Usage matches OpenAI SDK
response = client.openai.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello"}]
)
```

### Compatibility Rules
1. Match OpenAI method signatures exactly
2. Convert between formats transparently
3. Document any differences
4. Support streaming responses
5. Maintain backward compatibility