from __future__ import annotations

import pytest

from kamiwaza_sdk.artifacts.providers import get_provider

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


def test_qwen_snapshot_contains_readme(hf_cache_dir) -> None:
    provider = get_provider()
    snapshot_dir = provider.download(
        "mlx-community/Qwen3-4B-4bit",
        allow_patterns=["README.md"],
        cache_dir=hf_cache_dir,
    )
    readme = snapshot_dir / "README.md"
    assert readme.exists(), f"Missing README at {readme}"
    content = readme.read_text(encoding="utf-8")
    assert "mlx-community" in content
    assert "Qwen3-4B" in content
