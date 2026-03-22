.PHONY: build-python build-typescript build-all test-python test-typescript test-all \
       publish-python publish-typescript publish-test-python publish-test-typescript \
       clean-python clean-typescript clean-all

# Python
build-python:
	cd python && python -m build

test-python:
	cd python && pip install -e ".[dev]" && pytest tests/ -v

clean-python:
	cd python && rm -rf dist/ build/ *.egg-info kamiwaza_extensions_lib/*.egg-info

publish-python: clean-python build-python
	cd python && twine upload dist/*

publish-test-python: clean-python build-python
	cd python && twine upload --repository testpypi dist/*

verify-test-python:
	pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ kamiwaza-extensions-lib
	python -c "from kamiwaza_extensions_lib import Identity, KamiwazaClient, create_session_router; print('OK')"

# TypeScript
build-typescript:
	cd typescript && npm install && npm run build

test-typescript:
	cd typescript && npm install && npm run typecheck

clean-typescript:
	cd typescript && rm -rf dist/ node_modules/

pack-typescript: build-typescript
	cd typescript && npm pack

publish-typescript: build-typescript
	cd typescript && npm publish --access public

publish-test-typescript: build-typescript
	cd typescript && npm publish --access public --tag beta

# All
build-all: build-python build-typescript

test-all: test-python test-typescript

clean-all: clean-python clean-typescript
