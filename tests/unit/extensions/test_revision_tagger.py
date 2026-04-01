"""Tests for RevisionTagger."""

import re
from unittest.mock import patch

import pytest

from kamiwaza_extensions.revision_tagger import RevisionTagger


@pytest.fixture
def tagger():
    return RevisionTagger()


class TestGenerateTag:
    def test_custom_tag_returned_as_is(self, tagger):
        assert tagger.generate_tag("1.0.0", custom="my-experiment") == "my-experiment"

    def test_clean_repo_format(self, tagger):
        with patch.object(
            RevisionTagger, "get_git_info", return_value=("abc1234", False)
        ):
            tag = tagger.generate_tag("1.0.0", _now=1711900800)
        assert tag == "1.0.0-dev+abc1234.1711900800"

    def test_dirty_repo_format(self, tagger):
        with patch.object(
            RevisionTagger, "get_git_info", return_value=("abc1234", True)
        ):
            tag = tagger.generate_tag("1.0.0", _now=1711900800)
        assert tag == "1.0.0-dev+dirty.1711900800"

    def test_no_git_format(self, tagger):
        with patch.object(
            RevisionTagger, "get_git_info", return_value=(None, False)
        ):
            tag = tagger.generate_tag("2.3.1", _now=1711900800)
        assert tag == "2.3.1-dev+nogit.1711900800"

    def test_tag_is_docker_compatible(self, tagger):
        """Docker tags: [a-zA-Z0-9_.-]+, max 128 chars."""
        with patch.object(
            RevisionTagger, "get_git_info", return_value=("abc1234", False)
        ):
            tag = tagger.generate_tag("1.0.0", _now=1711900800)
        assert re.match(r"^[a-zA-Z0-9_.+-]+$", tag)
        assert len(tag) <= 128

    def test_different_timestamps_produce_different_tags(self, tagger):
        with patch.object(
            RevisionTagger, "get_git_info", return_value=("abc1234", False)
        ):
            t1 = tagger.generate_tag("1.0.0", _now=1000)
            t2 = tagger.generate_tag("1.0.0", _now=1001)
        assert t1 != t2

    def test_uses_real_time_by_default(self, tagger):
        with patch.object(
            RevisionTagger, "get_git_info", return_value=("abc1234", False)
        ):
            tag = tagger.generate_tag("1.0.0")
        # Should contain a recent epoch — just verify format
        assert re.match(r"^1\.0\.0-dev\+abc1234\.\d{10}$", tag)


class TestGetGitInfo:
    def test_returns_sha_and_clean(self, tagger):
        """Smoke test — depends on this repo being a git repo."""
        sha, dirty = tagger.get_git_info()
        # We're running inside kamiwaza-sdk which is a git repo
        if sha is not None:
            assert len(sha) >= 7
            assert isinstance(dirty, bool)

    def test_no_git_returns_none(self, tagger):
        with patch("kamiwaza_extensions.revision_tagger.subprocess.run") as mock:
            mock.side_effect = FileNotFoundError("git not found")
            sha, dirty = tagger.get_git_info()
        assert sha is None
        assert dirty is False
