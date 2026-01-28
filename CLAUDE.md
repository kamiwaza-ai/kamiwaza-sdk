# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kamiwaza SDK is a Python client library for the Kamiwaza AI Platform. It provides a type-safe, object-oriented interface to all Kamiwaza API endpoints with built-in authentication, error handling, and resource management.

## AI Development Guidance

This project uses a comprehensive AI assistance framework:
- **Core principles**: Follow @.ai/rules/core-principles.md for YAGNI, KISS, and fundamental practices
- **Code style**: Apply @.ai/rules/style.md for Python conventions and SDK patterns
- **Architecture patterns**: Use @.ai/rules/sdk-patterns.md for service design
- **Testing standards**: Follow @.ai/rules/testing.md for test requirements
- **Task templates**: Use prompts in @.ai/prompts/ for common tasks

See @.ai/README.md for the complete AI assistance framework.

## Common Development Commands

### Installation and Setup
- **Development install**: `uv sync`
- **Build package**: `uv build`
- **Run tests**: `uv run pytest`

### Code Quality
- **Format code**: `black kamiwaza_sdk/`
- **Sort imports**: `isort kamiwaza_sdk/`
- **Type checking**: `mypy kamiwaza_sdk/` (type stubs may need configuration)

### Testing
- **Run all tests**: `pytest`
- **Run with coverage**: `pytest --cov=kamiwaza_sdk --cov-report=html`
- **Run unit tests only**: `pytest -m "not integration"`
- **Run specific test**: `pytest tests/unit/services/test_models.py::TestModelsService::test_list_models`

## Architecture Overview

### Client-Service Pattern
The SDK uses a lazy-loading service architecture where `KamiwazaClient` orchestrates access to individual service modules. Services are instantiated only when first accessed, reducing memory footprint and startup time.

For detailed patterns, see @.ai/rules/sdk-patterns.md.

### Key Components

1. **KamiwazaClient** (`client.py`): Main entry point, provides lazy-loaded service properties
2. **BaseService** (`services/base_service.py`): Common functionality for all services
3. **AuthenticationManager** (`authentication.py`): Handles API keys, OAuth tokens, and refresh
4. **Schemas** (`schemas/`): Pydantic models for all API contracts
5. **Exceptions** (`exceptions.py`): Semantic error hierarchy mapping HTTP codes

### Service Composition
Complex services use mixin composition for modularity:
- `ModelsService` = BaseService + SearchMixin + DownloadMixin + FileMixin + ConfigMixin
- Each mixin handles a specific feature area
- See @.ai/knowledge/successful/service-patterns.md for why this works well

## Quick Start for New Contributors

### Adding a New Service
Use @.ai/prompts/add-service.md template:
```bash
"Add a new service for embeddings following @.ai/prompts/add-service.md"
```

### Extending Existing Service
Use @.ai/prompts/add-mixin.md template:
```bash
"Add batch operations to models service using @.ai/prompts/add-mixin.md"
```

### Debugging Issues
- **Test failures**: @.ai/prompts/fix-test.md
- **Auth problems**: @.ai/prompts/debug-auth.md
- **Performance**: @.ai/prompts/optimize-performance.md

## Important Implementation Notes

### Forward Compatibility
All Pydantic models must use `Config.extra = "allow"` to handle new API fields gracefully. See @.ai/knowledge/failures/common-pitfalls.md for what happens without this.

### Progress Tracking
Long-running operations (downloads, batch processing) should use the progress tracking framework with context managers. See examples in @.ai/knowledge/successful/service-patterns.md.

### OpenAI Compatibility
The `openai` service provides drop-in compatibility with OpenAI's client library, translating between Kamiwaza and OpenAI formats transparently.

## Testing Philosophy

Tests are mandatory - no exceptions. See @.ai/rules/testing.md for comprehensive testing standards.

Key points:
- Mock all HTTP calls in unit tests
- Test both success and error paths
- Maintain > 80% coverage on new code
- Integration tests marked with `@pytest.mark.integration`

## Common Pitfalls

Before implementing, review @.ai/knowledge/failures/common-pitfalls.md to avoid:
- Returning raw dicts instead of Pydantic models
- Forgetting pagination handling
- Mutable default arguments
- Missing forward compatibility

## Release Process

1. Update version in `pyproject.toml`
2. Run full test suite: `uv run pytest`
3. Build distribution: `uv build`
4. Use `release.sh` for automated release (requires proper PyPI credentials)