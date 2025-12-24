#!/bin/bash

# Exit on any error
set -euxo pipefail

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
    read -p "Continue without clearing notebook outputs? (y/n) " -n 1 -r
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

# Remove old builds
rm -rf dist/ build/ *.egg-info

# Build new package
python -m build

# Exit if clean-only mode is requested
if [[ ${CLEAN_ONLY:-} == "--clean-only" ]]; then
    echo "Clean-only mode: stopping before upload"
    exit 0
fi

# Upload to PyPI and require confirmation
read -p "Ready to upload to PyPI. Continue? (y/n) " -n 1 -r
echo    # Move to a new line
if [[ $REPLY =~ ^[Yy]$ ]]
then
    twine upload dist/*
else
    echo "Upload cancelled"
    exit 1
fi
