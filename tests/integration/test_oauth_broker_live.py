"""Integration tests for OAuth Broker service.

These tests verify the OAuth broker functionality including:
- App installation lifecycle
- Tool policy management
- Google OAuth flow
- Connection management
- Proxy endpoints (Gmail, Drive, Calendar)
- Ephemeral token minting

Note: Some tests require OAuth broker to be configured with Google credentials.
Tests will skip gracefully if not configured.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.oauth_broker import (
    AppInstallationCreate,
    AppInstallationUpdate,
    MintTokenRequest,
    ToolPolicyCreate,
    ToolPolicyUpdate,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


# ========== Helper Functions ==========


def oauth_broker_available(client) -> bool:
    """Check if OAuth broker is available and configured.

    Returns:
        True if broker is reachable, False otherwise.
    """
    try:
        client.oauth_broker.list_app_installations()
        return True
    except APIError as e:
        if e.status_code == 404:
            return False
        return False


def google_oauth_configured(client) -> bool:
    """Check if Google OAuth is configured."""
    try:
        app = client.oauth_broker.create_app_installation(
            AppInstallationCreate(
                name=f"test-check-{uuid.uuid4().hex[:8]}",
                description="Configuration check",
            )
        )
        # Clean up
        client.oauth_broker.delete_app_installation(app.id)
        return True
    except APIError:
        return False


# ========== Fixtures ==========


@pytest.fixture
def test_app(live_kamiwaza_client):
    """Create a test app installation and clean it up after the test."""
    if not oauth_broker_available(live_kamiwaza_client):
        pytest.skip("OAuth broker not available")

    app = live_kamiwaza_client.oauth_broker.create_app_installation(
        AppInstallationCreate(
            name=f"Test App {uuid.uuid4().hex[:8]}",
            description="SDK integration test app",
            allowed_tools=["test-tool", "gmail-reader", "drive-reader"],
        )
    )

    yield app

    # Cleanup
    try:
        live_kamiwaza_client.oauth_broker.delete_app_installation(app.id)
    except APIError:
        pass  # Best effort cleanup


@pytest.fixture
def test_policy(live_kamiwaza_client, test_app):
    """Create a test tool policy and clean it up after the test."""
    policy = live_kamiwaza_client.oauth_broker.create_tool_policy(
        ToolPolicyCreate(
            app_installation_id=test_app.id,
            tool_id="test-tool",
            provider="google",
            allowed_operations=["gmail.search", "gmail.getMessage"],
            allowed_scope_subset=["https://www.googleapis.com/auth/gmail.readonly"],
        )
    )

    yield policy

    # Cleanup
    try:
        live_kamiwaza_client.oauth_broker.delete_tool_policy(policy.id)
    except APIError:
        pass


# ========== App Installation Tests ==========


def test_create_app_installation(live_kamiwaza_client):
    """Test creating an app installation."""
    if not oauth_broker_available(live_kamiwaza_client):
        pytest.skip("OAuth broker not available")

    app = live_kamiwaza_client.oauth_broker.create_app_installation(
        AppInstallationCreate(
            name=f"Test App {uuid.uuid4().hex[:8]}",
            description="Integration test app",
            allowed_tools=["gmail-reader", "gmail-sender"],
            app_metadata={"test": True},
        )
    )

    try:
        assert app.id is not None
        assert app.name.startswith("Test App")
        assert app.description == "Integration test app"
        assert "gmail-reader" in app.allowed_tools
        assert "gmail-sender" in app.allowed_tools
        assert app.lifecycle_status == "active"
        assert app.app_metadata == {"test": True}
        assert app.created_at is not None
        assert isinstance(app.created_at, datetime)
    finally:
        # Cleanup
        live_kamiwaza_client.oauth_broker.delete_app_installation(app.id)


def test_list_app_installations(live_kamiwaza_client, test_app):
    """Test listing app installations."""
    result = live_kamiwaza_client.oauth_broker.list_app_installations()

    assert result.items is not None
    assert isinstance(result.items, list)
    assert result.total >= 1

    # Our test app should be in the list
    app_ids = [app.id for app in result.items]
    assert test_app.id in app_ids


def test_get_app_installation(live_kamiwaza_client, test_app):
    """Test getting a specific app installation."""
    app = live_kamiwaza_client.oauth_broker.get_app_installation(test_app.id)

    assert app.id == test_app.id
    assert app.name == test_app.name
    assert app.description == test_app.description
    assert app.lifecycle_status == test_app.lifecycle_status


def test_update_app_installation(live_kamiwaza_client, test_app):
    """Test updating an app installation."""
    updated = live_kamiwaza_client.oauth_broker.update_app_installation(
        test_app.id,
        AppInstallationUpdate(
            name="Updated Test App",
            description="Updated description",
            allowed_tools=["updated-tool"],
        ),
    )

    assert updated.id == test_app.id
    assert updated.name == "Updated Test App"
    assert updated.description == "Updated description"
    assert updated.allowed_tools == ["updated-tool"]
    assert updated.updated_at is not None


def test_delete_app_installation(live_kamiwaza_client):
    """Test deleting an app installation."""
    if not oauth_broker_available(live_kamiwaza_client):
        pytest.skip("OAuth broker not available")

    # Create an app to delete
    app = live_kamiwaza_client.oauth_broker.create_app_installation(
        AppInstallationCreate(
            name=f"Delete Test {uuid.uuid4().hex[:8]}",
            description="Will be deleted",
        )
    )

    # Delete it
    live_kamiwaza_client.oauth_broker.delete_app_installation(app.id)

    # Verify it's gone
    with pytest.raises(APIError) as exc_info:
        live_kamiwaza_client.oauth_broker.get_app_installation(app.id)

    assert exc_info.value.status_code == 404


# ========== Tool Policy Tests ==========


def test_create_tool_policy(live_kamiwaza_client, test_app):
    """Test creating a tool policy."""
    policy = live_kamiwaza_client.oauth_broker.create_tool_policy(
        ToolPolicyCreate(
            app_installation_id=test_app.id,
            tool_id="gmail-reader",
            provider="google",
            allowed_operations=["gmail.search", "gmail.getMessage", "gmail.labels.list"],
            allowed_scope_subset=["https://www.googleapis.com/auth/gmail.readonly"],
            policy_metadata={"version": "1.0"},
        )
    )

    try:
        assert policy.id is not None
        assert policy.app_installation_id == test_app.id
        assert policy.tool_id == "gmail-reader"
        assert policy.provider == "google"
        assert "gmail.search" in policy.allowed_operations
        assert "https://www.googleapis.com/auth/gmail.readonly" in policy.allowed_scope_subset
        assert policy.policy_metadata == {"version": "1.0"}
    finally:
        live_kamiwaza_client.oauth_broker.delete_tool_policy(policy.id)


def test_list_tool_policies(live_kamiwaza_client, test_app, test_policy):
    """Test listing tool policies."""
    result = live_kamiwaza_client.oauth_broker.list_tool_policies(app_id=test_app.id)

    assert result.items is not None
    assert isinstance(result.items, list)
    assert result.total >= 1

    # Our test policy should be in the list
    policy_ids = [p.id for p in result.items]
    assert test_policy.id in policy_ids


def test_get_tool_policy(live_kamiwaza_client, test_policy):
    """Test getting a specific tool policy."""
    policy = live_kamiwaza_client.oauth_broker.get_tool_policy(test_policy.id)

    assert policy.id == test_policy.id
    assert policy.tool_id == test_policy.tool_id
    assert policy.provider == test_policy.provider


def test_update_tool_policy(live_kamiwaza_client, test_policy):
    """Test updating a tool policy."""
    updated = live_kamiwaza_client.oauth_broker.update_tool_policy(
        test_policy.id,
        ToolPolicyUpdate(
            allowed_operations=["gmail.search"],
            allowed_scope_subset=["https://www.googleapis.com/auth/gmail.readonly"],
            policy_metadata={"updated": True},
        ),
    )

    assert updated.id == test_policy.id
    assert updated.allowed_operations == ["gmail.search"]
    assert updated.policy_metadata == {"updated": True}


def test_delete_tool_policy(live_kamiwaza_client, test_app):
    """Test deleting a tool policy."""
    # Create a policy to delete
    policy = live_kamiwaza_client.oauth_broker.create_tool_policy(
        ToolPolicyCreate(
            app_installation_id=test_app.id,
            tool_id="delete-test-tool",
            provider="google",
            allowed_operations=["gmail.search"],
        )
    )

    # Delete it
    live_kamiwaza_client.oauth_broker.delete_tool_policy(policy.id)

    # Verify it's gone
    with pytest.raises(APIError) as exc_info:
        live_kamiwaza_client.oauth_broker.get_tool_policy(policy.id)

    assert exc_info.value.status_code == 404


# ========== Google OAuth Flow Tests ==========


def test_start_google_auth(live_kamiwaza_client, test_app):
    """Test starting Google OAuth flow."""
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
    ]

    result = live_kamiwaza_client.oauth_broker.start_google_auth(test_app.id, scopes)

    assert result.auth_url is not None
    assert "accounts.google.com" in result.auth_url
    assert result.state is not None
    assert result.provider == "google"

    # Verify the auth URL contains expected parameters
    assert "client_id" in result.auth_url
    assert "redirect_uri" in result.auth_url
    assert "scope" in result.auth_url
    assert "state" in result.auth_url
    assert result.state in result.auth_url


def test_get_connection_status_disconnected(live_kamiwaza_client, test_app):
    """Test getting connection status when not connected."""
    status = live_kamiwaza_client.oauth_broker.get_connection_status(
        test_app.id, "google"
    )

    # Should show disconnected status
    assert status.status in ["disconnected", "needs_reauth", "revoked"]
    assert status.provider == "google"


# Note: Full OAuth callback flow requires user interaction with Google
# and cannot be fully automated. The following test demonstrates the API
# but will be skipped in CI environments.


@pytest.mark.skip(reason="Requires manual OAuth flow completion")
def test_google_oauth_callback(live_kamiwaza_client, test_app):
    """
    Test handling Google OAuth callback.

    This test is skipped by default as it requires manual OAuth flow completion.
    To run manually:
    1. Start OAuth flow with start_google_auth
    2. Complete authorization in browser
    3. Extract code and state from callback URL
    4. Pass to handle_google_callback
    """
    # Start OAuth flow
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    auth_result = live_kamiwaza_client.oauth_broker.start_google_auth(
        test_app.id, scopes
    )

    print(f"\nVisit this URL to authorize: {auth_result.auth_url}")
    print("After authorizing, paste the callback URL:")


# ========== Connection Management Tests ==========


# Note: These tests require an active OAuth connection
# They will be skipped if no connection exists


def test_disconnect_when_not_connected(live_kamiwaza_client, test_app):
    """Test disconnecting when no connection exists."""
    # Should handle gracefully (either succeed or return appropriate error)
    try:
        live_kamiwaza_client.oauth_broker.disconnect(test_app.id, "google")
    except APIError as e:
        # 404 is acceptable (no connection to disconnect)
        assert e.status_code == 404


# ========== Proxy Endpoint Tests ==========

# Note: Proxy endpoint tests require an active OAuth connection with appropriate scopes.
# These tests demonstrate the API but will skip if no connection is available.


@pytest.mark.skip(reason="Requires active Google OAuth connection")
def test_gmail_search(live_kamiwaza_client, test_app):
    """Test Gmail search proxy endpoint."""
    results = live_kamiwaza_client.oauth_broker.gmail_search(
        app_id=test_app.id,
        tool_id="gmail-reader",
        query="is:unread",
        max_results=5,
    )

    assert results is not None
    assert isinstance(results, dict)
    # Gmail API returns messages and resultSizeEstimate
    assert "resultSizeEstimate" in results or "messages" in results


@pytest.mark.skip(reason="Requires active Google OAuth connection")
def test_gmail_list_labels(live_kamiwaza_client, test_app):
    """Test Gmail list labels proxy endpoint."""
    result = live_kamiwaza_client.oauth_broker.gmail_list_labels(
        app_id=test_app.id, tool_id="gmail-reader"
    )

    assert result is not None
    assert isinstance(result, dict)
    assert "labels" in result


@pytest.mark.skip(reason="Requires active Google OAuth connection")
def test_drive_list_files(live_kamiwaza_client, test_app):
    """Test Drive list files proxy endpoint."""
    result = live_kamiwaza_client.oauth_broker.drive_list_files(
        app_id=test_app.id,
        tool_id="drive-reader",
        query="mimeType='application/pdf'",
        page_size=10,
    )

    assert result is not None
    assert isinstance(result, dict)
    assert "files" in result


@pytest.mark.skip(reason="Requires active Google OAuth connection")
def test_calendar_list_calendars(live_kamiwaza_client, test_app):
    """Test Calendar list calendars proxy endpoint."""
    result = live_kamiwaza_client.oauth_broker.calendar_list_calendars(
        app_id=test_app.id, tool_id="calendar-reader"
    )

    assert result is not None
    assert isinstance(result, dict)
    assert "items" in result


@pytest.mark.skip(reason="Requires active Google OAuth connection")
def test_calendar_list_events(live_kamiwaza_client, test_app):
    """Test Calendar list events proxy endpoint."""
    # Get events for the next 7 days
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=7)).isoformat()

    result = live_kamiwaza_client.oauth_broker.calendar_list_events(
        app_id=test_app.id,
        tool_id="calendar-reader",
        calendar_id="primary",
        time_min=time_min,
        time_max=time_max,
        max_results=20,
    )

    assert result is not None
    assert isinstance(result, dict)
    assert "items" in result or "summary" in result


# ========== Ephemeral Token Minting Tests ==========

# Note: Token minting tests require an active OAuth connection
# These tests demonstrate the API but will skip if no connection is available.


@pytest.mark.skip(reason="Requires active Google OAuth connection")
def test_mint_ephemeral_token(live_kamiwaza_client, test_app):
    """Test minting an ephemeral access token."""
    request = MintTokenRequest(
        app_installation_id=test_app.id,
        tool_id="test-tool",
        provider="google",
        lease_duration=300,  # 5 minutes
    )

    token = live_kamiwaza_client.oauth_broker.mint_ephemeral_token(request)

    assert token.access_token is not None
    assert token.lease_id is not None
    assert token.expires_in > 0
    assert token.broker_lease_expires_in == 300
    assert token.token_type == "Bearer"
    assert len(token.granted_scopes) > 0

    # Cleanup: revoke the lease
    live_kamiwaza_client.oauth_broker.revoke_lease(token.lease_id)


@pytest.mark.skip(reason="Requires active Google OAuth connection")
def test_get_lease_status(live_kamiwaza_client, test_app):
    """Test getting lease status."""
    # Mint a token first
    request = MintTokenRequest(
        app_installation_id=test_app.id,
        tool_id="test-tool",
        provider="google",
        lease_duration=300,
    )
    token = live_kamiwaza_client.oauth_broker.mint_ephemeral_token(request)

    try:
        # Get lease status
        lease = live_kamiwaza_client.oauth_broker.get_lease_status(token.lease_id)

        assert lease.lease_id == token.lease_id
        assert lease.app_installation_id == test_app.id
        assert lease.tool_id == "test-tool"
        assert lease.provider == "google"
        assert lease.is_valid is True
        assert lease.issued_at is not None
        assert lease.expires_at is not None
        assert lease.revoked_at is None
    finally:
        # Cleanup
        live_kamiwaza_client.oauth_broker.revoke_lease(token.lease_id)


@pytest.mark.skip(reason="Requires active Google OAuth connection")
def test_revoke_lease(live_kamiwaza_client, test_app):
    """Test revoking a token lease."""
    # Mint a token first
    request = MintTokenRequest(
        app_installation_id=test_app.id,
        tool_id="test-tool",
        provider="google",
        lease_duration=300,
    )
    token = live_kamiwaza_client.oauth_broker.mint_ephemeral_token(request)

    # Revoke the lease
    live_kamiwaza_client.oauth_broker.revoke_lease(token.lease_id)

    # Verify it's revoked
    lease = live_kamiwaza_client.oauth_broker.get_lease_status(token.lease_id)
    assert lease.is_valid is False
    assert lease.revoked_at is not None


# ========== Error Handling Tests ==========


def test_get_nonexistent_app(live_kamiwaza_client):
    """Test getting a nonexistent app installation."""
    if not oauth_broker_available(live_kamiwaza_client):
        pytest.skip("OAuth broker not available")

    fake_id = uuid.uuid4()
    with pytest.raises(APIError) as exc_info:
        live_kamiwaza_client.oauth_broker.get_app_installation(fake_id)

    assert exc_info.value.status_code == 404


def test_get_nonexistent_policy(live_kamiwaza_client):
    """Test getting a nonexistent tool policy."""
    if not oauth_broker_available(live_kamiwaza_client):
        pytest.skip("OAuth broker not available")

    fake_id = uuid.uuid4()
    with pytest.raises(APIError) as exc_info:
        live_kamiwaza_client.oauth_broker.get_tool_policy(fake_id)

    assert exc_info.value.status_code == 404


def test_invalid_provider(live_kamiwaza_client, test_app):
    """Test using an invalid provider."""
    with pytest.raises(APIError):
        live_kamiwaza_client.oauth_broker.get_connection_status(
            test_app.id, "invalid-provider"
        )
