.PHONY: build-python build-typescript build-all test-python test-typescript test-all publish-python publish-typescript

# Python
build-python:
	cd python && python -m build

test-python:
	cd python && pip install -e ".[dev]" && pytest tests/ -v

publish-python:
	cd python && python -m build && twine upload dist/*

# TypeScript
build-typescript:
	cd typescript && npm install && npm run build

test-typescript:
	cd typescript && npm install && npm run typecheck

publish-typescript:
	cd typescript && npm install && npm run build && npm publish --access public

# All
build-all: build-python build-typescript

test-all: test-python test-typescript
