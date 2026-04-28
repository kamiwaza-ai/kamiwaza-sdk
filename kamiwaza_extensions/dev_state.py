"""DevStateFile — `.kz-ext/dev-state.json` schema + read/write helpers.

Persists the last successful step of ``kz-ext dev`` so partial failures
(network blip during push, operator readiness flake, etc.) resume at the
next incomplete step on re-invocation rather than rebuilding from scratch.

Also provides the ``last_dev_name`` lookup that ``kz-ext status`` (with no
arguments) uses to find the most recently-deployed dev extension without
recomputing the dev-suffixed name from the user JWT.

Design reference: §4.2.9 ``DevStateFile`` + §4.7 dev/status UX mocks.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEV_STATE_DIR = ".kz-ext"
DEV_STATE_FILE = "dev-state.json"

# Steps written in execution order. Resuming with ``last_successful_step ==
# STEPS[i]`` means steps ``STEPS[:i+1]`` are done; the next step to attempt
# is ``STEPS[i+1]`` (or the loop has fully completed if ``i == len-1``).
STEPS: tuple[str, ...] = ("build", "push", "apply", "poll")


@dataclass
class DevState:
    """Persisted state of the most recent ``kz-ext dev`` invocation."""

    last_run_at: str = ""  # ISO 8601 UTC
    last_revision: str = ""
    last_dev_name: str = ""
    last_successful_step: str = ""  # one of STEPS, or "" for never-run
    cluster: str = ""
    extension_name: str = ""
    deployer: str = ""

    def is_step_complete(self, step: str) -> bool:
        if not self.last_successful_step:
            return False
        try:
            return STEPS.index(self.last_successful_step) >= STEPS.index(step)
        except ValueError:
            return False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def state_path(extension_dir: Path) -> Path:
    """Return the ``.kz-ext/dev-state.json`` path for ``extension_dir``."""
    return extension_dir / DEV_STATE_DIR / DEV_STATE_FILE


def read_state(extension_dir: Path) -> Optional[DevState]:
    """Read the dev-state file. Returns ``None`` if missing or unreadable.

    A corrupt or partially-written file is treated as missing — the
    re-invocation will simply start from scratch rather than resume from
    a state we can't trust.
    """
    path = state_path(extension_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    # Drop unknown keys so a forward-compat field added by a newer CLI
    # doesn't crash an older CLI on a shared workspace.
    fields = {f for f in DevState().__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in fields}
    return DevState(**filtered)


def write_state(extension_dir: Path, state: DevState) -> None:
    """Atomically write the dev-state file to ``extension_dir/.kz-ext/``."""
    dir_path = extension_dir / DEV_STATE_DIR
    dir_path.mkdir(parents=True, exist_ok=True)

    final = state_path(extension_dir)
    tmp = final.with_suffix(final.suffix + ".tmp")
    tmp.write_text(state.to_json() + "\n", encoding="utf-8")
    os.replace(tmp, final)


def mark_step(
    extension_dir: Path,
    step: str,
    *,
    revision: str,
    dev_name: str,
    cluster: str,
    extension_name: str,
    deployer: str,
) -> DevState:
    """Update the dev-state to record completion of ``step``.

    Loads the existing state (or creates a fresh one), updates the
    book-keeping fields, sets ``last_successful_step = step``, and writes
    the result atomically.
    """
    if step not in STEPS:
        raise ValueError(f"Unknown dev step '{step}'; expected one of {STEPS}")

    state = read_state(extension_dir) or DevState()
    state.last_run_at = datetime.now(timezone.utc).isoformat()
    state.last_revision = revision
    state.last_dev_name = dev_name
    state.last_successful_step = step
    state.cluster = cluster
    state.extension_name = extension_name
    state.deployer = deployer
    write_state(extension_dir, state)
    return state


def resume_message(state: Optional[DevState]) -> Optional[str]:
    """Return a one-line resume notice if the last run did not finish.

    Returns ``None`` when there's no prior state, or when the last run
    completed all steps.
    """
    if state is None or not state.last_successful_step:
        return None
    if state.last_successful_step == STEPS[-1]:
        return None
    return (
        f"Last deploy stopped at step '{state.last_successful_step}' on "
        f"{state.last_run_at} — resuming from next incomplete step."
    )
