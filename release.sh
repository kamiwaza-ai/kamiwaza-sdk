#!/bin/bash

# Exit on any error
set -euxo pipefail

# Check for --clean-only flag
CLEAN_ONLY=${1:-}

for folder in examples; do
    if [ -d "$folder" ]; then
        echo "Clearing notebook state in $folder..."
        
        # Clear notebook state if needed
        if [ "$folder" = "notebooks" ]; then
            echo "Clearing notebook state..."
            find notebooks -name "*.ipynb" -not -path "*/\.*" -exec ./notebook-venv/bin/jupyter nbconvert --to notebook --ClearOutputPreprocessor.enabled=True --inplace {} \;
        fi
        
    else
        echo "$folder directory does not exist, skipping..."
    fi
done

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
