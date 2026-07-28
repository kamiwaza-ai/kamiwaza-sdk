"""Shared ASGI launcher for App Garden extension backends.

Resolves the deployment's routing mode exactly like the frontend boot
entrypoint, then starts Uvicorn with ``root_path`` set to the runtime app
path. The upstream proxy must strip that public prefix before forwarding.
Uvicorn adds ``root_path`` to the ASGI scope, so apps keep declaring
unprefixed routes (``/api``, ``/health``) while URL generation and OpenAPI
servers see the external mount. Forwarding an already-prefixed transport path
while also setting ``root_path`` duplicates the prefix.

Usage (scaffold backend Dockerfile)::

    python -m kamiwaza_extensions_lib.asgi app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .runtime import RuntimeRouting

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kamiwaza_extensions_lib.asgi",
        description="Start an extension backend with runtime-path routing.",
    )
    parser.add_argument("app", help="ASGI application import string (module:attr)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    try:
        routing = RuntimeRouting.from_env()
    except ValueError as exc:
        print(f"[kz-asgi] FATAL: {exc}", file=sys.stderr)
        return 1

    import uvicorn  # Lazy: the generated backend declares uvicorn[standard].

    uvicorn.run(
        args.app,
        host=args.host,
        port=args.port,
        root_path=routing.root_path,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
