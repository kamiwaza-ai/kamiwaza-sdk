#!/bin/bash

# Release script for the three artifacts shipped from this repo:
#   1. kamiwaza-sdk            (PyPI, root pyproject)
#   2. kamiwaza-extensions-lib (PyPI, kamiwaza_extensions_lib/pyproject.toml)
#   3. @kamiwaza-ai/extensions-lib (npm, kamiwaza-ai-extensions-lib/)
#
# Each package gets its own confirmation prompt before upload. Bump the
# corresponding version (root pyproject / __init__.py / package.json)
# before running.

# Exit on any error. We deliberately do NOT enable `-x` here: `uv publish`
# inherits secrets like `UV_PUBLISH_TOKEN` from the environment, and tracing
# could leak them via stderr if the script ever expands such vars inline.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Check for --clean-only flag
CLEAN_ONLY=${1:-}

# Check for pipx (needed to clear notebook outputs)
if ! command -v pipx &> /dev/null; then
    echo "Error: pipx is required but not installed."
    echo ""
    echo "Install pipx with one of:"
    echo "  brew install pipx     # macOS"
    echo "  pip install pipx      # pip"
    echo "  apt install pipx      # Debian/Ubuntu"
    echo ""
    read -r -p "Continue without clearing notebook outputs? (y/n) " REPLY || REPLY=n
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
    SKIP_NOTEBOOK_CLEAR=1
else
    SKIP_NOTEBOOK_CLEAR=0
fi

if [[ $SKIP_NOTEBOOK_CLEAR -eq 0 ]]; then
    for folder in examples; do
        if [ -d "$folder" ]; then
            echo "Clearing notebook state in $folder..."
            find "${folder}" -name "*.ipynb" -not -path "*/\.*" -exec pipx run --spec nbconvert jupyter-nbconvert --to notebook --ClearOutputPreprocessor.enabled=True --inplace {} \;
        else
            echo "$folder directory does not exist, skipping..."
        fi
    done
else
    echo "Skipping notebook output clearing (pipx not available)"
fi

# Clean prior builds for all three packages.
# Both `uv build` invocations below write to ./dist/ regardless of CWD because
# `[tool.uv.workspace]` makes them workspace siblings. We split per-package
# artifacts into ./dist/sdk/ and ./dist/lib/ via --out-dir so each `uv publish`
# only sees its own files.
rm -rf -- \
    dist/ build/ *.egg-info \
    kamiwaza_extensions_lib/build/ kamiwaza_extensions_lib/*.egg-info \
    kamiwaza-ai-extensions-lib/dist/ kamiwaza-ai-extensions-lib/*.tgz

# --- Build all three artifacts ---

# 1. Python extensions-lib (built first because it's a runtime dep of the SDK)
uv build --package kamiwaza-extensions-lib --out-dir dist/lib

# 2. Python SDK
uv build --package kamiwaza-sdk --out-dir dist/sdk

# 3. TypeScript extensions-lib
( cd kamiwaza-ai-extensions-lib && npm ci && npm run build && npm pack )

# Exit if clean-only mode is requested
if [[ ${CLEAN_ONLY:-} == "--clean-only" ]]; then
    echo "Clean-only mode: stopping before upload"
    exit 0
fi

# --- Publish, one prompt per package ---
#
# Order matters: kamiwaza-sdk pins `kamiwaza-extensions-lib>=0.4,<0.5` as a
# runtime dep. Publishing the SDK first leaves users unable to
# `pip install kamiwaza-sdk==X` if the lib upload step is skipped or fails.
# So: lib → SDK → npm.

read -r -p "Upload kamiwaza-extensions-lib to PyPI? (y/n) " REPLY || REPLY=n
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    uv publish --check-url https://pypi.org/simple/ dist/lib/*
else
    echo "kamiwaza-extensions-lib upload skipped"
fi

read -r -p "Upload kamiwaza-sdk to PyPI? (y/n) " REPLY || REPLY=n
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    uv publish --check-url https://pypi.org/simple/ dist/sdk/*
else
    echo "kamiwaza-sdk upload skipped"
fi

read -r -p "Upload @kamiwaza-ai/extensions-lib to npm? (y/n) " REPLY || REPLY=n
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Pre-flight: confirm we're authenticated against the public npm registry
    # specifically. A stale .npmrc pointing at a private/internal registry
    # would otherwise divert the upload silently.
    NPM_REGISTRY="https://registry.npmjs.org/"
    if ! NPM_USER=$(npm whoami --registry="$NPM_REGISTRY" 2>&1); then
        echo "Error: not logged in to $NPM_REGISTRY"
        echo "  npm whoami output: $NPM_USER"
        echo "  Run \`npm login --registry=$NPM_REGISTRY\` and re-run this script."
        exit 1
    fi
    echo "Publishing @kamiwaza-ai/extensions-lib as npm user: $NPM_USER"
    ( cd kamiwaza-ai-extensions-lib && npm publish --access public --registry="$NPM_REGISTRY" )
else
    echo "@kamiwaza-ai/extensions-lib upload skipped"
fi
