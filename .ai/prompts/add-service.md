# Add New Service to SDK

I need to add a new service for `{SERVICE_NAME}` that will handle {DESCRIPTION}.

## Requirements

The service should:
- Follow the SDK's lazy-loading service pattern
- Include proper Pydantic schemas for all API contracts
- Implement comprehensive error handling
- Add appropriate type hints
- Include unit tests with mocked HTTP calls

## API Endpoints to Implement

The service will implement these endpoints:
- `{METHOD} /api/v1/{PATH}` - {ENDPOINT_DESCRIPTION}

## Implementation Checklist

Please:
1. First check if similar services exist in `kamiwaza_client/services/`
2. Review the base service pattern in `base_service.py`
3. Create schema models in `kamiwaza_client/schemas/{SERVICE_NAME}.py`
4. Implement service in `kamiwaza_client/services/{SERVICE_NAME}.py`
5. Add service property to `KamiwazaClient` class
6. Create unit tests in `tests/unit/services/test_{SERVICE_NAME}.py`
7. Update `__init__.py` exports as needed

## Schema Structure Example

```python
# schemas/{SERVICE_NAME}.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class {ResourceName}(BaseModel):
    """Single resource representation."""
    id: str
    name: str
    # Add fields based on API response
    
    class Config:
        extra = "allow"  # Forward compatibility

class {ResourceName}List(BaseModel):
    """Paginated list response."""
    items: List[{ResourceName}]
    total: int
    page: int = 1
    per_page: int = 20
```

## Service Structure Example

```python
# services/{SERVICE_NAME}.py
from typing import List, Optional
from kamiwaza_client.services.base_service import BaseService
from kamiwaza_client.schemas.{SERVICE_NAME} import {ResourceName}, {ResourceName}List

class {ServiceName}Service(BaseService):
    """Service for {SERVICE_NAME} operations."""
    
    def list(self, **kwargs) -> {ResourceName}List:
        """List {RESOURCES}."""
        response = self._request("GET", "/{SERVICE_NAME}", params=kwargs)
        return {ResourceName}List(**response)
    
    def get(self, resource_id: str) -> {ResourceName}:
        """Get specific {RESOURCE}."""
        response = self._request("GET", f"/{SERVICE_NAME}/{resource_id}")
        return {ResourceName}(**response)
```

## Testing Template

```python
# tests/unit/services/test_{SERVICE_NAME}.py
import pytest
from unittest.mock import Mock
from kamiwaza_client.services.{SERVICE_NAME} import {ServiceName}Service
from kamiwaza_client.schemas.{SERVICE_NAME} import {ResourceName}

class Test{ServiceName}Service:
    @pytest.fixture
    def service(self):
        client = Mock()
        return {ServiceName}Service(client)
    
    def test_list_{RESOURCES}(self, service):
        # Test implementation
        pass
```