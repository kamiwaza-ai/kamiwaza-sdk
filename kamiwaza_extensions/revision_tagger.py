"""Dev revision tag generation from git state + timestamp."""

from __future__ import annotations

import subprocess
import time
from typing import Optional, Tuple


class RevisionTagger:
    """Generate unique Docker image tags for dev builds.

    Tag format:
    - Clean repo:  ``{version}-dev+{sha7}.{epoch}``
    - Dirty repo:  ``{version}-dev+dirty.{epoch}``
    - No git:      ``{version}-dev+nogit.{epoch}``
    - Custom:      whatever the caller passes via *custom*
    """

    def generate_tag(
        self,
        version: str,
        custom: Optional[str] = None,
        *,
        _now: Optional[int] = None,
    ) -> str:
        """Return a unique dev revision tag.

        Args:
            version: Base version from kamiwaza.json (e.g. ``"1.0.0"``).
            custom: If provided, returned as-is (``--revision`` flag).
            _now: Override epoch for deterministic tests.
        """
        if custom:
            return custom

        ts = _now if _now is not None else int(time.time())
        sha, dirty = self.get_git_info()

        if sha is None:
            slug = "nogit"
        elif dirty:
            slug = "dirty"
        else:
            slug = sha

        return f"{version}-dev+{slug}.{ts}"

    @staticmethod
    def get_git_info() -> Tuple[Optional[str], bool]:
        """Return ``(short_sha | None, is_dirty)``.

        Returns ``(None, False)`` when the working directory is not inside a
        git repository or git is not installed.
        """
        try:
            sha = (
                subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                .stdout.strip()
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return None, False

        try:
            status = (
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                .stdout.strip()
            )
            dirty = bool(status)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            dirty = False

        return sha, dirty
