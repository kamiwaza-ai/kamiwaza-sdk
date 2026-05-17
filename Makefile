.PHONY: sync test test-unit test-live lint format type-check build clean docs help

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
	@echo "  docs       - Generate API reference docs from typed signatures (T5.16)"
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

# Documentation
docs: sync
	# T5.16 / ENG-4744 — autogen API reference from typed signatures + docstrings.
	# pdoc reads each module's __doc__, class/function signatures, and Google-
	# style docstrings; the M3 surface (kamiwaza.subjects, kamiwaza.datasets,
	# kamiwaza.cluster execution-gate methods) carries full type hints, so this
	# render produces a complete reference without hand-written doc pages.
	#
	# Submodules listed explicitly: kamiwaza/__init__.py doesn't re-import the
	# full surface (lazy-loaded service modules), so pdoc auto-discovery from
	# the top-level package alone wouldn't traverse into them.
	uv run pdoc --output-directory docs/api \
		kamiwaza \
		kamiwaza.client \
		kamiwaza.exceptions \
		kamiwaza.models \
		kamiwaza.cluster \
		kamiwaza.datasets \
		kamiwaza.federations \
		kamiwaza.gates \
		kamiwaza.jobs \
		kamiwaza.retrieval \
		kamiwaza.subjects

# Build and release
build: clean
	uv build

clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache/ .mypy_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
