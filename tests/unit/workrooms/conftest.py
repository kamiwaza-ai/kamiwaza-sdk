"""Fixtures shared by the ENG-12325 workroom unit tests.

They live here rather than in a helper module because pytest binds a fixture
by the name visible where the test is collected: an autouse fixture imported
into a module applies only there, and a fixture requested by argument collides
with the import under the linter's redefinition rule.
"""

from __future__ import annotations

import pytest
from tests.integration import _workroom_disposable_user as disposable
from tests.unit.workrooms._user_fakes import TRACE_ENV


@pytest.fixture(autouse=True)
def no_fake_account_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The accounts here are fakes, so none should be named in the warnings summary."""
    monkeypatch.setattr(disposable, "_announce_account", lambda _username: None)


@pytest.fixture
def untraced(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Clear the HTTP-trace environment a developer may have left set."""
    for name, _value in TRACE_ENV.values():
        monkeypatch.delenv(name, raising=False)
    return monkeypatch
