# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kamiwaza SDK is a Python client library for the Kamiwaza AI Platform. It provides a type-safe, object-oriented interface to all Kamiwaza API endpoints with built-in authentication, error handling, and resource management.

## Common Development Commands

### Installation and Setup
- **Development install**: `pip install -e .`
- **Install dependencies**: `pip install -r requirements.txt`
- **Build package**: `python setup.py sdist bdist_wheel`

### Code Quality
- **Format code**: `black kamiwaza_sdk/`
- **Sort imports**: `isort kamiwaza_sdk/`
- **Type checking**: `mypy kamiwaza_sdk/` (type stubs may need configuration)

## Architecture and Code Organization

### Client-Service Architecture
The SDK uses a lazy-loading service pattern where the main `KamiwazaClient` class acts as an orchestrator:

```python
# kamiwaza_sdk/client.py
class KamiwazaClient:
    @property
    def models(self) -> ModelsService:
        # Services are instantiated only when accessed
```

### Service Layer Pattern
Every service inherits from `BaseService` and follows this structure:
- **Service Class**: Business logic and API endpoint methods
- **Schema Module**: Pydantic models for request/response validation
- **Mixins** (for complex services): Modular functionality (see `services/models/`)

### Authentication Flow
1. `KamiwazaClient` accepts API key or OAuth credentials
2. `AuthenticationManager` handles token refresh and retry logic
3. Each service inherits authenticated HTTP client from `BaseService`
4. 401 errors trigger automatic token refresh

### Key Architectural Decisions

#### Mixin-Based Service Design
The `models` service demonstrates sophisticated mixin composition:
```
ModelsService = BaseService + SearchMixin + DownloadMixin + FileMixin + ConfigMixin + CompatibilityMixin
```
This pattern allows modular feature composition while keeping individual mixins focused.

#### Type Safety Throughout
- All API contracts defined as Pydantic models in `schemas/`
- Comprehensive type hints for IDE support
- Runtime validation of inputs and outputs

#### Error Handling Hierarchy
Custom exceptions in `exceptions.py` map HTTP errors to semantic exceptions:
- `KamiwazaAPIError` - Base exception
- `AuthenticationError` - 401/403 responses
- `ResourceNotFoundError` - 404 responses
- `ValidationError` - 400/422 responses

## Development Patterns

### Adding New Service Endpoints
1. Define Pydantic schemas in `schemas/{service_name}/`
2. Add method to service class with proper type hints
3. Use `self._request()` for HTTP calls (handles auth/retry)
4. Update service documentation in `docs/services/`

### Progress Tracking
The SDK includes sophisticated progress tracking:
- `DownloadTracker` - Tracks multi-file downloads
- `ProgressFormatter` - Rich terminal output
- Used for user feedback during long operations

## Testing Guidelines

**Current State**: No test infrastructure exists. When adding tests:

### Recommended Test Structure
```
tests/
├── unit/
│   ├── services/
│   │   ├── test_models.py
│   │   └── test_serving.py
│   ├── test_client.py
│   └── test_authentication.py
├── integration/
│   └── test_api_endpoints.py
└── conftest.py
```

### Testing Patterns
- Mock HTTP responses using `responses` or `httpx` test client
- Test both success and error scenarios
- Validate Pydantic schema serialization/deserialization

## Important Implementation Notes

### Service Lazy Loading
Services are properties that instantiate on first access. This pattern:
- Reduces initial client creation overhead
- Allows partial SDK usage without loading all services
- Maintains single instance per service per client

### Authentication State
- API keys stored in `AuthenticationManager`
- OAuth tokens cached with automatic refresh
- Never log or expose authentication credentials

### OpenAI Compatibility
The `openai` service provides drop-in compatibility:
```python
# Can be used with OpenAI client libraries
client.openai.chat.completions.create(...)
```

## Code Style

- **Black**: 88 character line length
- **isort**: Black-compatible profile
- **Type hints**: Required for all public methods
- **Docstrings**: Google style for complex methods
- **No print statements**: Use logging