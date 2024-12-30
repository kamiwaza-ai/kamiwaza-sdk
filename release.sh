#!/bin/bash

# Exit on any error
set -e

# Remove old builds
rm -rf dist/ build/ *.egg-info

# Build new package
python -m build

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
