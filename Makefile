.PHONY: sync test test-unit test-live lint format type-check build clean help

# Default target
help:
	@echo "Available targets:"
	@echo "  sync       - Install dependencies (uv sync)"
	@echo "  test       - Run unit tests"
	@echo "  test-unit  - Run unit tests only"
	@echo "  test-live  - Run live integration tests"
	@echo "  lint       - Run ruff linter"
	@echo "  format     - Format code with black and isort"
	@echo "  type-check - Run mypy type checker"
	@echo "  build      - Build package"
	@echo "  clean      - Remove build artifacts"

# Dependency management
sync:
	uv sync

# Testing
test: sync
	uv run pytest -m "not integration and not live and not e2e"

test-unit: sync
	uv run pytest -m "unit"

test-live: sync
	uv run pytest -m "live"

# Code quality
lint: sync
	uv run ruff check kamiwaza_sdk/

format: sync
	uv run black kamiwaza_sdk/ tests/
	uv run isort kamiwaza_sdk/ tests/

type-check: sync
	uv run mypy kamiwaza_sdk/

# Build and release
build: clean
	uv build

clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache/ .mypy_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
