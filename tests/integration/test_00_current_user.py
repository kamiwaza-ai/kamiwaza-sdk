from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


def test_current_user_is_resolvable(live_kamiwaza_client) -> None:
    user = live_kamiwaza_client.auth.get_current_user()
    print(f"Current user: {user.model_dump()}")
    assert user.sub
