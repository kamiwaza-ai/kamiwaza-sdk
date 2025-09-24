# Add Mixin to Existing Service

I need to add a new mixin for the `{SERVICE_NAME}` service that adds {FEATURE} functionality.

## Context

The {SERVICE_NAME} service currently has these mixins:
- {EXISTING_MIXINS}

I need to add a new mixin that will:
- {MIXIN_PURPOSE}

## Implementation Steps

Please:
1. Review existing mixins in `kamiwaza_sdk/services/{SERVICE_NAME}/`
2. Create new mixin file: `kamiwaza_sdk/services/{SERVICE_NAME}/{MIXIN_NAME}.py`
3. Add mixin to service class inheritance
4. Update schemas if new models are needed
5. Add unit tests for new functionality
6. Ensure proper type hints throughout

## Mixin Template

```python
# services/{SERVICE_NAME}/{MIXIN_NAME}.py
from typing import List, Optional, Dict, Any
from kamiwaza_sdk.schemas.{SERVICE_NAME} import {NewSchema}

class {MixinName}Mixin:
    """Mixin for {FEATURE} functionality."""
    
    def {method_name}(self, **kwargs) -> {ReturnType}:
        """{METHOD_DESCRIPTION}
        
        Args:
            {ARG_NAME}: {ARG_DESCRIPTION}
            
        Returns:
            {RETURN_DESCRIPTION}
            
        Raises:
            {ERROR_TYPE}: {ERROR_CONDITION}
        """
        # Implementation using self._request()
        response = self._request("POST", f"/{SERVICE_NAME}/{ENDPOINT}", json=kwargs)
        return {ReturnType}(**response)
```

## Integration Example

```python
# services/{SERVICE_NAME}/base.py
from kamiwaza_sdk.services.base_service import BaseService
from .{EXISTING_MIXIN} import {ExistingMixin}
from .{MIXIN_NAME} import {MixinName}Mixin  # Add new import

class {ServiceName}Service(
    BaseService,
    {ExistingMixin},
    {MixinName}Mixin  # Add to inheritance
):
    """Service with all mixins."""
    pass
```

## Testing Requirements

Tests should cover:
- Normal operation of new methods
- Error handling (404, 400, etc.)
- Edge cases specific to the feature
- Integration with existing functionality