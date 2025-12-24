"""Utilities for persisting Kamiwaza access tokens on disk."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class StoredToken:
    """Lightweight representation of a cached access token."""

    access_token: str
    refresh_token: Optional[str]
    expires_at: float  # epoch seconds

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= time.time()


class TokenStore:
    """Abstract token persistence layer."""

    def load(self) -> Optional[StoredToken]:  # pragma: no cover - interface
        raise NotImplementedError

    def save(self, token: StoredToken) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def clear(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class FileTokenStore(TokenStore):
    """Persist tokens as JSON under ~/.kamiwaza/token.json by default."""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        default_path = Path(os.environ.get("KAMIWAZA_TOKEN_PATH", Path.home() / ".kamiwaza" / "token.json"))
        self.path = Path(path) if path else default_path

    def load(self) -> Optional[StoredToken]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            return None
        try:
            return StoredToken(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expires_at=float(data["expires_at"]),
            )
        except KeyError:
            return None

    def save(self, token: StoredToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(token), handle)
        tmp_path.replace(self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
