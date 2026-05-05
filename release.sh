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

# Mode flag. Default is full release (clean → build → publish prompts).
#   --build-only : run clean + build for all three packages, then exit
#                  before any publish prompts. Useful for verifying that
#                  artifacts assemble correctly without touching PyPI/npm.
#   (Legacy: --clean-only is accepted as an alias for --build-only — its
#    name was misleading because it always built artifacts before exiting.)
MODE=${1:-}
case "$MODE" in
    --build-only|--clean-only) BUILD_ONLY=1 ;;
    "") BUILD_ONLY=0 ;;
    *)
        echo "Error: unknown flag '$MODE'"
        echo "Usage: $0 [--build-only]"
        exit 1
        ;;
esac

# Required tooling. Fail loudly up front rather than dying mid-build with
# a cryptic "command not found".
for tool in uv npm; do
    if ! command -v "$tool" &> /dev/null; then
        echo "Error: '$tool' is required but not installed."
        echo "  Install: https://github.com/astral-sh/uv (uv) | https://nodejs.org (npm)"
        exit 1
    fi
done

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

# Stop before publish if --build-only was requested
if [[ $BUILD_ONLY -eq 1 ]]; then
    echo "Build-only mode: artifacts ready in dist/sdk/, dist/lib/, kamiwaza-ai-extensions-lib/*.tgz"
    exit 0
fi

# --- Publish, one prompt per package ---
#
# Order matters: kamiwaza-sdk pins `kamiwaza-extensions-lib>=0.4,<0.5` as a
# runtime dep. Publishing the SDK first leaves users unable to
# `pip install kamiwaza-sdk==X` if the lib upload step is skipped or fails.
# So: lib → SDK → npm.

LIB_PUBLISHED=0

read -r -p "Upload kamiwaza-extensions-lib to PyPI? (y/n) " REPLY || REPLY=n
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    uv publish --check-url https://pypi.org/simple/ dist/lib/*
    LIB_PUBLISHED=1
else
    echo "kamiwaza-extensions-lib upload skipped"
fi

# Coupling guard: kamiwaza-sdk's runtime dep is kamiwaza-extensions-lib
# at the version we just (or didn't) publish. If the operator skipped the
# lib but tries to publish the SDK, `pip install kamiwaza-sdk==X` would
# fail to resolve until the lib lands in a separate run. Refuse unless
# the operator explicitly acknowledges that the required lib version is
# *already* on PyPI.
if [[ $LIB_PUBLISHED -eq 0 ]]; then
    echo
    echo "Note: kamiwaza-sdk requires kamiwaza-extensions-lib (>=0.4,<0.5) at runtime."
    echo "  Since the lib upload was skipped, only proceed if that version is"
    echo "  already on PyPI from a prior release."
    read -r -p "Is the required lib version already on PyPI? (y/n) " REPLY || REPLY=n
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Skipping kamiwaza-sdk upload to avoid publishing an unresolvable release."
        SDK_GATED=1
    else
        SDK_GATED=0
    fi
else
    SDK_GATED=0
fi

if [[ $SDK_GATED -eq 0 ]]; then
    read -r -p "Upload kamiwaza-sdk to PyPI? (y/n) " REPLY || REPLY=n
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        uv publish --check-url https://pypi.org/simple/ dist/sdk/*
    else
        echo "kamiwaza-sdk upload skipped"
    fi
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
