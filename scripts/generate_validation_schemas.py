#!/usr/bin/env python3
"""Regenerate committed validation-provider JSON Schema resources."""

from __future__ import annotations

import json
from pathlib import Path

from kamiwaza_sdk.validation.schema_export import (
    SCHEMA_FILENAMES,
    SCHEMA_MODELS,
    schema_document,
)

SCHEMA_DIR = Path(__file__).parents[1] / "kamiwaza_sdk/validation/schemas"


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for schema_id, model_type in SCHEMA_MODELS.items():
        document = schema_document(schema_id, model_type)
        destination = SCHEMA_DIR / SCHEMA_FILENAMES[schema_id]
        destination.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
