"""Regression coverage for catalog-stack fixture setup (ENG-12305)."""

import os
import subprocess
from pathlib import Path

import pytest

SETUP_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "integration"
    / "catalog_stack"
    / "setup-test-data.sh"
)
PARQUET_FIXTURES = (
    "inline-small.parquet",
    "inline-large.parquet",
    "inline-large-sse.parquet",
)


@pytest.mark.parametrize("missing_fixture", PARQUET_FIXTURES)
def test_seed_marker_rejects_missing_parquet_fixture(
    tmp_path: Path, missing_fixture: str
) -> None:
    """A prior false-success marker must not hide missing advertised objects."""
    state_dir = tmp_path / "state"
    state_data = state_dir / "test-data"
    source_data = tmp_path / "source" / "test-data"
    state_data.mkdir(parents=True)
    source_data.mkdir(parents=True)
    (state_dir / ".seed-complete").touch()
    for fixture in PARQUET_FIXTURES:
        if fixture != missing_fixture:
            (state_data / fixture).write_bytes(b"fixture")

    result = subprocess.run(
        ["bash", str(SETUP_SCRIPT)],
        env={
            **os.environ,
            "STATE_DIR": str(state_dir),
            "DATA_DIR": str(source_data.parent),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert missing_fixture in result.stderr


def test_missing_parquet_dependencies_fail_before_stack_setup(tmp_path: Path) -> None:
    """Setup must reject absent parquet data before reporting stack success."""
    state_dir = tmp_path / "state"
    source_data = tmp_path / "source" / "test-data"
    source_data.mkdir(parents=True)
    (source_data / "sample.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(SETUP_SCRIPT)],
        env={
            **os.environ,
            "STATE_DIR": str(state_dir),
            "DATA_DIR": str(source_data.parent),
            "PYTHON_BIN": "/bin/false",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Missing required parquet fixture" in result.stderr
    assert not (state_dir / ".seed-complete").exists()


def test_seed_marker_accepts_complete_parquet_fixtures(tmp_path: Path) -> None:
    """A complete prior seed still returns without starting containers."""
    state_dir = tmp_path / "state"
    data_dir = tmp_path / "source" / "test-data"
    state_data = state_dir / "test-data"
    data_dir.mkdir(parents=True)
    state_data.mkdir(parents=True)
    (state_dir / ".seed-complete").touch()
    for fixture in PARQUET_FIXTURES:
        (state_data / fixture).write_bytes(b"fixture")

    result = subprocess.run(
        ["bash", str(SETUP_SCRIPT)],
        env={
            **os.environ,
            "STATE_DIR": str(state_dir),
            "DATA_DIR": str(data_dir.parent),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "already seeded" in result.stdout
