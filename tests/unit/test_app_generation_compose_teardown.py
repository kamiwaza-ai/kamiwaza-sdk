"""Unit cover for the app-generation module's compose teardown.

ENG-12432. Three properties, none of which needs Docker to pin:

* A nonzero exit is **reported**. An earlier spelling discarded the return code,
  so a failed teardown left detached containers and named volumes on a shared
  Docker host while the test stayed green.
* The Compose binary comes from the same ``detect_compose_command`` kz-ext uses
  to start the stack. Hardcoding the v2 plugin would fail teardown on a v1-only
  host where the capability itself works.
* The project is named explicitly. Compose otherwise derives the name from the
  directory basename, which ``tmp_path_factory`` repeats across runs, so one
  run's ``down -v`` could destroy another's containers and volumes.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration import test_app_generation_path as appgen

PROJECT = "eng12432-appgen-abcd1234"


def _fake_compose(
    monkeypatch: pytest.MonkeyPatch,
    *results: tuple[int, str],
    binary: list[str] | None = None,
):
    """Replace the module's subprocess and binary detection.

    Returns the list of commands actually run, so the assertions are about what
    would reach Docker rather than about the helper's return value alone.
    """
    commands: list[list[str]] = []
    outcomes = iter(results)

    def run(command, *, cwd, text, capture_output, timeout, check):
        assert check is False, "check=True would raise instead of being reported"
        commands.append([*command, f"cwd={cwd}"])
        returncode, stderr = next(outcomes)
        return SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")

    monkeypatch.setattr(appgen, "subprocess", SimpleNamespace(run=run))
    monkeypatch.setattr(
        appgen, "detect_compose_command", lambda: binary or ["docker", "compose"]
    )
    return commands


def test_a_clean_teardown_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = _fake_compose(monkeypatch, (0, ""))
    assert appgen._down_compose_stacks([(Path("/stacks/one"), PROJECT)]) == []
    assert commands == [
        ["docker", "compose", "-p", PROJECT, "down", "-v", "cwd=/stacks/one"]
    ]


def test_the_teardown_names_the_project_it_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without -p, Compose would resolve a name shared with other runs."""
    commands = _fake_compose(monkeypatch, (0, ""))
    appgen._down_compose_stacks([(Path("/stacks/one"), PROJECT)])
    assert "-p" in commands[0]
    assert commands[0][commands[0].index("-p") + 1] == PROJECT


def test_the_teardown_uses_the_detected_compose_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A v1-only host runs ``docker-compose``, which is what started the stack."""
    commands = _fake_compose(monkeypatch, (0, ""), binary=["docker-compose"])
    appgen._down_compose_stacks([(Path("/stacks/one"), PROJECT)])
    assert commands[0][:1] == ["docker-compose"]


def test_a_failed_teardown_is_reported_not_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_compose(monkeypatch, (1, "network in use\n"))
    failures = appgen._down_compose_stacks([(Path("/stacks/one"), PROJECT)])
    assert len(failures) == 1
    assert "/stacks/one" in failures[0]
    assert PROJECT in failures[0]
    assert "exited 1" in failures[0]
    assert "network in use" in failures[0]


def test_one_failed_stack_does_not_abandon_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every stack is taken down even after one fails, or the rest leak."""
    commands = _fake_compose(monkeypatch, (1, "boom"), (0, ""))
    failures = appgen._down_compose_stacks(
        [(Path("/stacks/one"), PROJECT), (Path("/stacks/two"), "eng12432-appgen-ef56")]
    )
    assert [c[-1] for c in commands] == ["cwd=/stacks/one", "cwd=/stacks/two"]
    assert len(failures) == 1
