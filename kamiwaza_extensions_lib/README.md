# kamiwaza-extensions-lib

Runtime library for Kamiwaza extensions. Provides FastAPI auth middleware,
identity extraction, a typed Kamiwaza model client, and session management
for extension backends.

This package is intentionally separate from `kamiwaza-sdk`: extension
backends need a lightweight async library, not the full SDK with its sync
HTTP client and 20+ service modules.

## Install

```bash
pip install 'kamiwaza-extensions-lib>=0.4,<0.5'
```

## Usage

```python
from kamiwaza_extensions_lib import (
    KamiwazaExtClient,
    require_auth,
    extract_identity,
)
```

See `CHANGELOG.md` in this directory for release notes.
