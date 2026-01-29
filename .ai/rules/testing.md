# Testing Standards for Kamiwaza SDK

## Test Structure

```
tests/
├── unit/
│   ├── test_client.py
│   ├── test_authentication.py
│   ├── services/
│   │   ├── test_models.py
│   │   ├── test_serving.py
│   │   └── test_openai.py
│   └── schemas/
│       ├── test_model_schemas.py
│       └── test_serving_schemas.py
├── integration/
│   ├── test_api_endpoints.py
│   └── test_auth_flow.py
└── conftest.py
```

## Testing Principles

### Test Categories
1. **Unit Tests**: Fast, isolated, no external dependencies
2. **Integration Tests**: Test against real API (marked with `@pytest.mark.integration`)
3. **No E2E Tests**: SDK doesn't need end-to-end tests

### Coverage Requirements
- Minimum 80% coverage for new code
- 100% coverage for error paths
- All public methods must have tests

## Unit Testing Patterns

### Basic Service Test
```python
import pytest
from unittest.mock import Mock, patch
from kamiwaza_sdk.services.models import ModelsService
from kamiwaza_sdk.exceptions import ResourceNotFoundError

class TestModelsService:
    @pytest.fixture
    def mock_client(self):
        """Create mock HTTP client."""
        client = Mock()
        client.request = Mock()
        return client
    
    @pytest.fixture
    def service(self, mock_client):
        """Create service with mocked client."""
        return ModelsService(mock_client)
    
    def test_get_model_success(self, service, mock_client):
        """Test successful model retrieval."""
        # Arrange
        mock_client.request.return_value = {
            "id": "model-123",
            "name": "Test Model",
            "family": "llama"
        }
        
        # Act
        model = service.get("model-123")
        
        # Assert
        assert model.id == "model-123"
        assert model.name == "Test Model"
        mock_client.request.assert_called_once_with(
            "GET", "/models/model-123"
        )
    
    def test_get_model_not_found(self, service, mock_client):
        """Test 404 handling."""
        # Arrange
        mock_client.request.side_effect = ResourceNotFoundError(
            "Model not found"
        )
        
        # Act & Assert
        with pytest.raises(ResourceNotFoundError):
            service.get("nonexistent")
```

### Schema Testing
```python
import pytest
from datetime import datetime
from kamiwaza_sdk.schemas.models import Model, ModelList

class TestModelSchemas:
    def test_model_from_dict(self):
        """Test model creation from API response."""
        data = {
            "id": "model-123",
            "name": "Test Model",
            "family": "llama",
            "created_at": "2024-01-01T00:00:00Z",
            "extra_field": "ignored"  # Forward compatibility
        }
        
        model = Model(**data)
        
        assert model.id == "model-123"
        assert isinstance(model.created_at, datetime)
        assert hasattr(model, "extra_field")  # Extra fields preserved
    
    def test_model_validation(self):
        """Test schema validation."""
        with pytest.raises(ValueError):
            Model(id="123", name="")  # Empty name should fail
```

### Authentication Testing
```python
@pytest.fixture
def auth_manager():
    """Create auth manager for testing."""
    return AuthenticationManager(api_key="test-key")

def test_api_key_headers(auth_manager):
    """Test API key authentication."""
    headers = auth_manager.get_headers()
    assert headers == {"X-API-Key": "test-key"}

def test_oauth_token_refresh(auth_manager, mock_requests):
    """Test OAuth token refresh flow."""
    # Setup expired token
    auth_manager.token = "expired"
    auth_manager.refresh_token = "refresh"
    
    # Mock refresh endpoint
    mock_requests.post.return_value.json.return_value = {
        "access_token": "new-token",
        "expires_in": 3600
    }
    
    # Trigger refresh
    auth_manager.handle_auth_error(Mock(status_code=401))
    
    # Verify
    assert auth_manager.token == "new-token"
```

## Mocking Patterns

### Mock HTTP Responses
```python
@pytest.fixture
def mock_response():
    """Create mock response object."""
    def _mock(status_code=200, json_data=None, text=""):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = json_data or {}
        response.text = text
        response.raise_for_status = Mock()
        if status_code >= 400:
            response.raise_for_status.side_effect = HTTPError()
        return response
    return _mock
```

### Mock Progress Tracking
```python
@patch('kamiwaza_sdk.utils.progress.tqdm')
def test_download_with_progress(mock_tqdm, service):
    """Test progress tracking during download."""
    # Progress should be updated
    service.download_model("model-123", Path("/tmp/model"))
    
    # Verify progress bar created
    mock_tqdm.assert_called_once()
    mock_tqdm.return_value.update.assert_called()
```

## Test Fixtures

### Common Fixtures
```python
# conftest.py
import pytest
from kamiwaza_sdk import KamiwazaClient

@pytest.fixture
def client():
    """Create test client."""
    return KamiwazaClient(api_key="test-key")

@pytest.fixture
def mock_requests(monkeypatch):
    """Mock requests library."""
    import requests
    mock = Mock()
    monkeypatch.setattr(requests, "request", mock)
    return mock

@pytest.fixture
def temp_dir(tmp_path):
    """Create temporary directory for downloads."""
    return tmp_path / "kamiwaza_test"
```

## Testing Best Practices

### Do's
- ✅ Test one behavior per test
- ✅ Use descriptive test names
- ✅ Mock external dependencies
- ✅ Test error conditions
- ✅ Use fixtures for common setup

### Don'ts
- ❌ Don't test implementation details
- ❌ Don't make actual API calls in unit tests
- ❌ Don't use sleep() in tests
- ❌ Don't depend on test order
- ❌ Don't share state between tests

## Error Path Testing

### Required Error Tests
```python
def test_network_timeout(service, mock_client):
    """Test timeout handling."""
    mock_client.request.side_effect = TimeoutError()
    
    with pytest.raises(KamiwazaAPIError) as exc_info:
        service.get("model-123")
    
    assert "timeout" in str(exc_info.value).lower()

def test_invalid_json_response(service, mock_client):
    """Test malformed response handling."""
    mock_client.request.return_value = "not json"
    
    with pytest.raises(KamiwazaAPIError):
        service.get("model-123")

def test_rate_limit_handling(service, mock_client):
    """Test 429 rate limit response."""
    mock_client.request.side_effect = KamiwazaAPIError(
        "Rate limit exceeded", status_code=429
    )
    
    with pytest.raises(KamiwazaAPIError) as exc_info:
        service.get("model-123")
    
    assert exc_info.value.status_code == 429
```

## Integration Testing

### Mark Integration Tests
```python
@pytest.mark.integration
class TestModelsIntegration:
    """Tests that hit real API."""
    
    @pytest.fixture
    def real_client(self):
        """Create client with real credentials."""
        # Get from env or skip test
        api_key = os.getenv("KAMIWAZA_TEST_API_KEY")
        if not api_key:
            pytest.skip("No test API key")
        return KamiwazaClient(api_key=api_key)
    
    def test_list_models_real(self, real_client):
        """Test actual API call."""
        models = real_client.models.list()
        assert isinstance(models.items, list)
        assert models.total >= 0
```

### Integration Test Guidelines
1. Skip if credentials not available
2. Use test/staging environment
3. Clean up created resources
4. Don't depend on specific data
5. Mark clearly with `@pytest.mark.integration`

## Test Commands

This project uses **uv** for dependency management. Use `make` targets or `uv run` to execute tests.

```bash
# Preferred: Makefile targets
make test                    # Unit + contract tests (excludes integration/live/e2e)
make test-unit               # Unit tests only (-m "unit")
make test-live               # Live integration tests (-m "live")

# Direct uv commands
uv run pytest                                          # All tests
uv run pytest --cov=kamiwaza_sdk --cov-report=html     # With coverage
uv run pytest -m "not integration and not live"        # Unit tests only
uv run pytest tests/unit/services/test_models.py       # Specific file
uv run pytest -v                                       # Verbose output
uv run pytest --lf                                     # Failed tests from last run
```

## Continuous Integration

### GitHub Actions Example
```yaml
- name: Install uv
  uses: astral-sh/setup-uv@v4

- name: Install dependencies
  run: uv sync

- name: Run tests
  run: uv run pytest --cov=kamiwaza_sdk --cov-report=xml

- name: Upload coverage
  uses: codecov/codecov-action@v3
  with:
    file: ./coverage.xml
```