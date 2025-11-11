from __future__ import annotations

from pathlib import Path

import pytest

from kamiwaza_sdk.artifacts.providers import (
    ArtifactProvider,
    get_provider,
    register_provider,
)

pytestmark = pytest.mark.unit


class DummyProvider(ArtifactProvider):
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def download(self, repo_id: str, **kwargs):
        self.calls.append((repo_id, kwargs))
        path = Path("dummy")
        return path


def test_register_and_fetch_provider(monkeypatch):
    dummy = DummyProvider()
    register_provider("dummy", dummy)

    provider = get_provider("dummy")
    assert provider is dummy

    path = provider.download("demo/model", cache_dir="tmp")

    assert path == Path("dummy")
    assert dummy.calls[0][0] == "demo/model"


def test_default_provider_downloads_snapshot(monkeypatch, tmp_path):
    provider = get_provider()

    def fake_snapshot(repo_id, **kwargs):
        assert repo_id == "mlx-community/Qwen3-4B-4bit"
        target = tmp_path / "snapshot"
        target.mkdir()
        (target / "README.md").write_text("mlx-community")
        return str(target)

    monkeypatch.setattr("kamiwaza_sdk.artifacts.providers.snapshot_download", fake_snapshot)
    path = provider.download(
        "mlx-community/Qwen3-4B-4bit",
        allow_patterns=["README.md"],
        cache_dir=tmp_path,
    )
    readme = path / "README.md"
    assert readme.exists()
