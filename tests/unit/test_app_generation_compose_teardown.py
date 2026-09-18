"""Unit cover for the app-generation module's compose teardown.

ENG-12432. The teardown's one job beyond running ``docker compose down -v`` is
to notice when it fails: an earlier spelling discarded the return code, so a
failed teardown left detached containers and named volumes on a shared Docker
host while the test stayed green. That check is asserted here rather than left
to a Docker host to demonstrate.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration import test_app_generation_path as appgen


def _fake_subprocess(monkeypatch: pytest.MonkeyPatch, *results: tuple[int, str]):
    """Replace the module's subprocess with one returning ``results`` in order."""
    calls: list[Path] = []
    outcomes = iter(results)

    def run(command, *, cwd, text, capture_output, timeout, check):
        assert command == ["docker", "compose", "down", "-v"]
        assert check is False, "check=True would raise instead of being reported"
        calls.append(cwd)
        returncode, stderr = next(outcomes)
        return SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")

    monkeypatch.setattr(appgen, "subprocess", SimpleNamespace(run=run))
    return calls


def test_a_clean_teardown_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_subprocess(monkeypatch, (0, ""))
    assert appgen._down_compose_stacks([Path("/stacks/one")]) == []
    assert calls == [Path("/stacks/one")]


def test_a_failed_teardown_is_reported_not_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_subprocess(monkeypatch, (1, "network in use\n"))
    failures = appgen._down_compose_stacks([Path("/stacks/one")])
    assert len(failures) == 1
    assert "/stacks/one" in failures[0]
    assert "exited 1" in failures[0]
    assert "network in use" in failures[0]


def test_one_failed_stack_does_not_abandon_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every stack is taken down even after one fails, or the rest leak."""
    calls = _fake_subprocess(monkeypatch, (1, "boom"), (0, ""))
    failures = appgen._down_compose_stacks([Path("/stacks/one"), Path("/stacks/two")])
    assert calls == [Path("/stacks/one"), Path("/stacks/two")]
    assert len(failures) == 1
