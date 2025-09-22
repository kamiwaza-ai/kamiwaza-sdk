# Code Style Rules for Kamiwaza SDK

## General Python Style

### Formatting
- **Black**: Line length 88 characters
- **isort**: Black-compatible profile
- **Type hints**: Required for all public methods and functions
- **Docstrings**: Google style for complex methods

### Naming Conventions
- **Classes**: PascalCase (e.g., `KamiwazaClient`, `ModelsService`)
- **Functions/Methods**: snake_case (e.g., `download_model`, `get_deployment`)
- **Constants**: UPPER_SNAKE_CASE (e.g., `DEFAULT_TIMEOUT`, `API_VERSION`)
- **Private methods**: Leading underscore (e.g., `_request`, `_validate_config`)

## SDK-Specific Style

### Service Method Patterns
```python
# Public methods return domain objects
def list_models(self, **kwargs) -> List[Model]:
    response = self._request("GET", "/models", params=kwargs)
    return [Model(**item) for item in response["items"]]

# Private methods handle internals
def _request(self, method: str, path: str, **kwargs) -> dict:
    # Implementation details
```

### Progress Tracking Pattern
```python
# Use context managers for progress
with ProgressTracker() as tracker:
    for item in items:
        tracker.update(item)
        # Process item
```

### Error Handling Pattern
```python
try:
    response = self._request(...)
except requests.HTTPError as e:
    if e.response.status_code == 404:
        raise ResourceNotFoundError(f"Model {model_id} not found")
    raise KamiwazaAPIError(f"API request failed: {e}")
```

## Code Complexity Limits

### Size Restrictions
- **Functions**: 50 lines max (including docstring)
- **Classes**: 300 lines max
- **Files**: 500 lines max
- **Methods per class**: 15 public methods max

### Cyclomatic Complexity
- **Per function**: Maximum 10
- **Nested depth**: Maximum 4 levels

When limits are exceeded:
1. Extract helper methods
2. Create mixins for related functionality
3. Split into multiple modules

## Import Organization

```python
# Standard library
import os
from typing import Optional, List, Dict, Any
from pathlib import Path

# Third-party
import requests
from pydantic import BaseModel, Field

# Kamiwaza SDK
from kamiwaza_client.exceptions import KamiwazaAPIError
from kamiwaza_client.schemas.models import Model
from kamiwaza_client.services.base_service import BaseService

# Relative imports (within same package)
from .mixins import SearchMixin
from .utils import format_response
```

## Documentation Standards

### Module Docstrings
```python
"""
Module description in one line.

Longer description explaining the module's purpose,
main classes, and usage examples if relevant.
"""
```

### Class Docstrings
```python
class ServiceClass:
    """One-line summary.
    
    Longer description if needed.
    
    Attributes:
        client: HTTP client instance
        base_url: API base URL
    """
```

### Method Docstrings (Complex Methods Only)
```python
def complex_method(self, param1: str, param2: Optional[int] = None) -> Dict[str, Any]:
    """One-line summary.
    
    Args:
        param1: Description of param1
        param2: Description of param2 (default: None)
        
    Returns:
        Description of return value
        
    Raises:
        ValueError: When param1 is invalid
        APIError: When API request fails
    """
```

## Type Annotation Rules

### Always Annotate
- All public method parameters and returns
- Class attributes in `__init__`
- Module-level constants

### Common Patterns
```python
# Optional parameters
def method(self, required: str, optional: Optional[str] = None) -> bool:

# Variable arguments
def method(self, *args: str, **kwargs: Any) -> None:

# Complex returns
def method(self) -> Dict[str, List[Model]]:

# Async methods
async def method(self) -> List[Model]:
```

## Testing Style

### Test Naming
```python
def test_should_return_models_when_valid_request():
def test_should_raise_error_when_model_not_found():
def test_should_handle_pagination_correctly():
```

### Test Organization
```python
class TestModelsService:
    """Group related tests in classes."""
    
    def test_list_models(self):
        """Each test method tests one behavior."""
        
    def test_download_model(self):
        """Keep tests focused and independent."""
```

## Anti-Patterns to Avoid

### Don't Use
- `print()` statements (use logging)
- Mutable default arguments
- Bare `except:` clauses
- Global variables
- Hard-coded credentials or URLs

### Don't Create
- God classes (doing too much)
- Deep inheritance hierarchies
- Circular imports
- Side effects in property getters

## Performance Considerations

### Lazy Loading
```python
@property
def expensive_resource(self):
    if not hasattr(self, '_expensive_resource'):
        self._expensive_resource = self._load_resource()
    return self._expensive_resource
```

### Batch Operations
```python
# Good: Single request for multiple items
models = client.models.list(ids=model_ids)

# Bad: Multiple requests
models = [client.models.get(id) for id in model_ids]
```