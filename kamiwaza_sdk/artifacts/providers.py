"""Artifact provider abstractions for downloading model assets."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional

from huggingface_hub import snapshot_download


class ArtifactProvider:
    """Base provider API for retrieving model artifacts."""

    def download(
        self,
        repo_id: str,
        *,
        allow_patterns: Optional[Iterable[str]] = None,
        revision: Optional[str] = None,
        cache_dir: Optional[Path | str] = None,
    ) -> Path:
        raise NotImplementedError


class HuggingFaceArtifactProvider(ArtifactProvider):
    """Downloads artifacts from Hugging Face Hub via snapshot_download."""

    def download(
        self,
        repo_id: str,
        *,
        allow_patterns: Optional[Iterable[str]] = None,
        revision: Optional[str] = None,
        cache_dir: Optional[Path | str] = None,
    ) -> Path:
        path = snapshot_download(
            repo_id,
            revision=revision,
            allow_patterns=allow_patterns,
            cache_dir=cache_dir,
            repo_type="model",
        )
        return Path(path)


_providers: Dict[str, ArtifactProvider] = {
    "huggingface": HuggingFaceArtifactProvider(),
}


def register_provider(name: str, provider: ArtifactProvider) -> None:
    """Register a named provider."""

    _providers[name] = provider


def get_provider(name: str = "huggingface") -> ArtifactProvider:
    """Return a provider by name."""

    if name not in _providers:
        raise KeyError(f"Unknown artifact provider '{name}'")
    return _providers[name]
