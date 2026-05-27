from __future__ import annotations

import os

import pytest

from .common import bootstrap_state_candidates, logger
from .state import LiveRoutedIntegrationState


def load_live_routed_integration_state() -> LiveRoutedIntegrationState | None:
    if os.getenv("RUN_LIVE_EXTENSION_TESTS") != "1":
        return None
    try:
        candidates = bootstrap_state_candidates()
    except FileNotFoundError as exc:
        # No REPO_ROOT or sibling deploy/ checkout available — skip live
        # tests cleanly rather than failing the session. CI environments
        # without the cross-repo bootstrap artifact opt in to live mode
        # via env var but legitimately can't materialize the state file.
        pytest.skip(f"No bootstrap state available for live tests: {exc}")
        return None
    searched_candidates = [str(candidate) for candidate in candidates]
    invalid_candidates: list[str] = []
    for candidate in candidates:
        try:
            candidate_exists = candidate.exists()
        except OSError as exc:
            invalid_candidates.append(f"{candidate}: {exc}")
            continue
        if not candidate_exists:
            continue
        try:
            return LiveRoutedIntegrationState.from_path(candidate)
        except (TypeError, ValueError) as exc:
            logger.warning("Ignoring invalid bootstrap state at %s: %s", candidate, exc)
            invalid_candidates.append(f"{candidate}: {exc}")
    # Invalid bootstrap state IS a fail (we found a file but it's malformed
    # — the operator clearly intended to provide one). Missing entirely is
    # a clean skip (no file found across any candidate path).
    if invalid_candidates:
        pytest.fail(
            "No usable bootstrap state found. "
            f"Searched: {', '.join(searched_candidates)}. "
            f"Invalid: {' | '.join(invalid_candidates)}"
        )
        return None
    pytest.skip(
        "RUN_LIVE_EXTENSION_TESTS=1 but no bootstrap state was found. "
        f"Searched: {', '.join(searched_candidates)}"
    )
    return None
