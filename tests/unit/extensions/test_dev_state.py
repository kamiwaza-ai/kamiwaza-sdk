"""Tests for DevStateFile (.kz-ext/dev-state.json) — ENG-3887 / §4.2.9."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kamiwaza_extensions.dev_state import (
    STEPS,
    DevState,
    mark_step,
    read_state,
    resume_message,
    state_path,
    write_state,
)


@pytest.mark.unit
class TestStatePathLocation:
    def test_lives_under_dot_kz_ext(self, tmp_path):
        p = state_path(tmp_path)
        assert p.parent.name == ".kz-ext"
        assert p.name == "dev-state.json"


@pytest.mark.unit
class TestReadWrite:
    def test_read_returns_none_when_missing(self, tmp_path):
        assert read_state(tmp_path) is None

    def test_write_then_read_round_trip(self, tmp_path):
        s = DevState(
            last_run_at="2026-04-28T20:00:00+00:00",
            last_revision="1.0.0-dev-abc123.1234567890",
            last_dev_name="hello-dev-b1e8a0",
            last_successful_step="apply",
            cluster="https://kamiwaza.test/api",
            extension_name="hello",
            deployer="jonathan@kamiwaza.ai",
        )
        write_state(tmp_path, s)
        out = read_state(tmp_path)
        assert out == s

    def test_write_creates_dot_kz_ext_dir(self, tmp_path):
        write_state(tmp_path, DevState(last_dev_name="x"))
        assert (tmp_path / ".kz-ext").is_dir()
        assert (tmp_path / ".kz-ext" / "dev-state.json").is_file()

    def test_corrupt_file_treated_as_missing(self, tmp_path):
        # An older or partially-written file shouldn't crash a re-invocation.
        path = state_path(tmp_path)
        path.parent.mkdir()
        path.write_text("not valid json {{{")
        assert read_state(tmp_path) is None

    def test_unknown_keys_ignored(self, tmp_path):
        # Forward compatibility: a future CLI may add fields. An older CLI
        # reading the same file must drop unknowns rather than crash.
        path = state_path(tmp_path)
        path.parent.mkdir()
        path.write_text(json.dumps({
            "last_dev_name": "hello-dev-x",
            "last_successful_step": "apply",
            "future_field": "ignored",
        }))
        out = read_state(tmp_path)
        assert out is not None
        assert out.last_dev_name == "hello-dev-x"
        assert out.last_successful_step == "apply"


@pytest.mark.unit
class TestMarkStep:
    def test_invalid_step_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown dev step"):
            mark_step(
                tmp_path, "wrong",
                revision="x", dev_name="y", cluster="c",
                extension_name="e", deployer="d",
            )

    def test_records_step_and_metadata(self, tmp_path):
        s = mark_step(
            tmp_path, "build",
            revision="1.0.0-dev-abc", dev_name="hello-dev-b1",
            cluster="https://k.test/api",
            extension_name="hello", deployer="jonathan@kamiwaza.ai",
        )
        assert s.last_successful_step == "build"
        assert s.last_revision == "1.0.0-dev-abc"
        assert s.last_dev_name == "hello-dev-b1"
        assert s.deployer == "jonathan@kamiwaza.ai"
        # Persisted on disk
        assert read_state(tmp_path) == s

    def test_progresses_step_in_order(self, tmp_path):
        for step in STEPS:
            mark_step(
                tmp_path, step,
                revision="x", dev_name="y", cluster="c",
                extension_name="e", deployer="d",
            )
        out = read_state(tmp_path)
        assert out is not None
        assert out.last_successful_step == STEPS[-1]


@pytest.mark.unit
class TestIsStepComplete:
    def test_empty_state_no_steps_complete(self):
        s = DevState()
        for step in STEPS:
            assert s.is_step_complete(step) is False

    def test_apply_implies_build_and_push(self):
        s = DevState(last_successful_step="apply")
        assert s.is_step_complete("build") is True
        assert s.is_step_complete("push") is True
        assert s.is_step_complete("apply") is True
        assert s.is_step_complete("poll") is False


@pytest.mark.unit
class TestResumeMessage:
    def test_no_state_no_message(self):
        assert resume_message(None) is None

    def test_completed_run_no_message(self):
        # Last run finished all the way through poll — nothing to resume.
        s = DevState(
            last_successful_step="poll",
            last_run_at="2026-04-28T20:00:00+00:00",
        )
        assert resume_message(s) is None

    def test_partial_run_emits_resume_notice(self):
        s = DevState(
            last_successful_step="push",
            last_run_at="2026-04-28T20:00:00+00:00",
        )
        msg = resume_message(s)
        assert msg is not None
        assert "push" in msg
        assert "2026-04-28" in msg
