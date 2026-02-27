"""Unit tests for OAuth Broker service."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from kamiwaza_sdk.schemas.oauth_broker import (
    AppInstallationCreate,
    AppInstallationUpdate,
    MintTokenRequest,
    ToolPolicyCreate,
    ToolPolicyUpdate,
)
from kamiwaza_sdk.services.oauth_broker import OAuthBrokerService

pytestmark = pytest.mark.unit


# ========== App Installation Tests ==========


def test_create_app_installation(dummy_client):
    """Test creating an app installation."""
    app_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "id": app_id,
        "name": "Test App",
        "description": "Test description",
        "owner_user_id": user_id,
        "lifecycle_status": "active",
        "allowed_tools": ["tool1", "tool2"],
        "app_metadata": {"key": "value"},
        "created_at": now,
        "updated_at": None,
        "deleted_at": None,
    }
    responses = {("post", "/oauth-broker/apps"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    app_request = AppInstallationCreate(
        name="Test App",
        description="Test description",
        allowed_tools=["tool1", "tool2"],
        app_metadata={"key": "value"},
    )
    result = service.create_app_installation(app_request)

    assert result.id == uuid.UUID(app_id)
    assert result.name == "Test App"
    assert result.description == "Test description"
    assert result.lifecycle_status == "active"

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/oauth-broker/apps"
    assert kwargs["json"]["name"] == "Test App"
    assert kwargs["json"]["allowed_tools"] == ["tool1", "tool2"]


def test_list_app_installations(dummy_client):
    """Test listing app installations."""
    app_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "items": [
            {
                "id": app_id,
                "name": "Test App",
                "description": None,
                "owner_user_id": user_id,
                "lifecycle_status": "active",
                "allowed_tools": [],
                "app_metadata": None,
                "created_at": now,
                "updated_at": None,
                "deleted_at": None,
            }
        ],
        "total": 1,
    }
    responses = {("get", "/oauth-broker/apps"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.list_app_installations(limit=50, offset=10)

    assert len(result.items) == 1
    assert result.total == 1
    assert result.items[0].name == "Test App"

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/apps"
    assert kwargs["params"]["limit"] == 50
    assert kwargs["params"]["offset"] == 10


def test_get_app_installation(dummy_client):
    """Test getting a specific app installation."""
    app_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "id": app_id,
        "name": "Test App",
        "description": "Test description",
        "owner_user_id": user_id,
        "lifecycle_status": "active",
        "allowed_tools": ["tool1"],
        "app_metadata": None,
        "created_at": now,
        "updated_at": None,
        "deleted_at": None,
    }
    responses = {("get", f"/oauth-broker/apps/{app_id}"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.get_app_installation(uuid.UUID(app_id))

    assert result.id == uuid.UUID(app_id)
    assert result.name == "Test App"

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == f"/oauth-broker/apps/{app_id}"


def test_update_app_installation(dummy_client):
    """Test updating an app installation."""
    app_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "id": app_id,
        "name": "Updated App",
        "description": "Updated description",
        "owner_user_id": user_id,
        "lifecycle_status": "active",
        "allowed_tools": ["updated-tool"],
        "app_metadata": None,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    responses = {("patch", f"/oauth-broker/apps/{app_id}"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    update_request = AppInstallationUpdate(
        name="Updated App",
        description="Updated description",
        allowed_tools=["updated-tool"],
    )
    result = service.update_app_installation(uuid.UUID(app_id), update_request)

    assert result.name == "Updated App"
    assert result.description == "Updated description"

    method, path, kwargs = client.calls[0]
    assert method == "patch"
    assert path == f"/oauth-broker/apps/{app_id}"
    assert kwargs["json"]["name"] == "Updated App"


def test_delete_app_installation(dummy_client):
    """Test deleting an app installation."""
    app_id = str(uuid.uuid4())

    responses = {("delete", f"/oauth-broker/apps/{app_id}"): None}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    service.delete_app_installation(uuid.UUID(app_id))

    method, path, kwargs = client.calls[0]
    assert method == "delete"
    assert path == f"/oauth-broker/apps/{app_id}"


# ========== Tool Policy Tests ==========


def test_create_tool_policy(dummy_client):
    """Test creating a tool policy."""
    policy_id = str(uuid.uuid4())
    app_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "id": policy_id,
        "app_installation_id": app_id,
        "tool_id": "test-tool",
        "provider": "google",
        "allowed_operations": ["gmail.search"],
        "allowed_scope_subset": ["https://www.googleapis.com/auth/gmail.readonly"],
        "policy_metadata": None,
        "created_at": now,
        "updated_at": None,
    }
    responses = {("post", "/oauth-broker/tool-policies"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    policy_request = ToolPolicyCreate(
        app_installation_id=uuid.UUID(app_id),
        tool_id="test-tool",
        provider="google",
        allowed_operations=["gmail.search"],
        allowed_scope_subset=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    result = service.create_tool_policy(policy_request)

    assert result.id == uuid.UUID(policy_id)
    assert result.tool_id == "test-tool"
    assert result.provider == "google"

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/oauth-broker/tool-policies"
    assert kwargs["json"]["tool_id"] == "test-tool"


def test_list_tool_policies(dummy_client):
    """Test listing tool policies."""
    policy_id = str(uuid.uuid4())
    app_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "items": [
            {
                "id": policy_id,
                "app_installation_id": app_id,
                "tool_id": "test-tool",
                "provider": "google",
                "allowed_operations": [],
                "allowed_scope_subset": [],
                "policy_metadata": None,
                "created_at": now,
                "updated_at": None,
            }
        ],
        "total": 1,
    }
    responses = {("get", "/oauth-broker/tool-policies"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.list_tool_policies(app_id=uuid.UUID(app_id), limit=50)

    assert len(result.items) == 1
    assert result.total == 1

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/tool-policies"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["limit"] == 50


def test_get_tool_policy(dummy_client):
    """Test getting a specific tool policy."""
    policy_id = str(uuid.uuid4())
    app_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "id": policy_id,
        "app_installation_id": app_id,
        "tool_id": "test-tool",
        "provider": "google",
        "allowed_operations": ["gmail.search"],
        "allowed_scope_subset": ["https://www.googleapis.com/auth/gmail.readonly"],
        "policy_metadata": None,
        "created_at": now,
        "updated_at": None,
    }
    responses = {("get", f"/oauth-broker/tool-policies/{policy_id}"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.get_tool_policy(uuid.UUID(policy_id))

    assert result.id == uuid.UUID(policy_id)
    assert result.tool_id == "test-tool"
    assert result.provider == "google"

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == f"/oauth-broker/tool-policies/{policy_id}"


def test_update_tool_policy(dummy_client):
    """Test updating a tool policy."""
    policy_id = str(uuid.uuid4())
    app_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "id": policy_id,
        "app_installation_id": app_id,
        "tool_id": "test-tool",
        "provider": "google",
        "allowed_operations": ["gmail.search"],
        "allowed_scope_subset": ["https://www.googleapis.com/auth/gmail.readonly"],
        "policy_metadata": {"updated": True},
        "created_at": now,
        "updated_at": now,
    }
    responses = {("patch", f"/oauth-broker/tool-policies/{policy_id}"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    update_request = ToolPolicyUpdate(
        allowed_operations=["gmail.search"],
        policy_metadata={"updated": True},
    )
    result = service.update_tool_policy(uuid.UUID(policy_id), update_request)

    assert result.id == uuid.UUID(policy_id)
    assert result.policy_metadata == {"updated": True}

    method, path, kwargs = client.calls[0]
    assert method == "patch"
    assert path == f"/oauth-broker/tool-policies/{policy_id}"
    assert kwargs["json"]["allowed_operations"] == ["gmail.search"]


def test_delete_tool_policy(dummy_client):
    """Test deleting a tool policy."""
    policy_id = str(uuid.uuid4())

    responses = {("delete", f"/oauth-broker/tool-policies/{policy_id}"): None}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    service.delete_tool_policy(uuid.UUID(policy_id))

    method, path, kwargs = client.calls[0]
    assert method == "delete"
    assert path == f"/oauth-broker/tool-policies/{policy_id}"


# ========== Google OAuth Flow Tests ==========


def test_start_google_auth(dummy_client):
    """Test starting Google OAuth flow."""
    app_id = str(uuid.uuid4())

    response = {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=test&state=xyz",
        "state": "xyz",
        "provider": "google",
    }
    responses = {("get", "/oauth-broker/auth/google/start"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    result = service.start_google_auth(uuid.UUID(app_id), scopes)

    assert result.auth_url.startswith("https://accounts.google.com")
    assert result.state == "xyz"
    assert result.provider == "google"

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/auth/google/start"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["scopes"] == "https://www.googleapis.com/auth/gmail.readonly"


def test_handle_google_callback(dummy_client):
    """Test handling Google OAuth callback."""
    connection_id = str(uuid.uuid4())
    app_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    response = {
        "id": connection_id,
        "app_installation_id": app_id,
        "user_id": user_id,
        "provider": "google",
        "external_user_id": "12345",
        "external_email": "user@example.com",
        "granted_scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        "expires_at": expires,
        "status": "connected",
        "created_at": now,
        "updated_at": None,
        "last_used_at": None,
        "last_refreshed_at": None,
    }
    responses = {("get", "/oauth-broker/auth/google/callback"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.handle_google_callback(
        code="auth_code", state="xyz.abc.def", scope="https://www.googleapis.com/auth/gmail.readonly"
    )

    assert result.id == uuid.UUID(connection_id)
    assert result.provider == "google"
    assert result.status == "connected"
    assert result.external_email == "user@example.com"

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/auth/google/callback"
    assert kwargs["params"]["code"] == "auth_code"
    assert kwargs["params"]["state"] == "xyz.abc.def"


# ========== Connection Management Tests ==========


def test_get_connection_status(dummy_client):
    """Test getting connection status."""
    app_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    response = {
        "status": "connected",
        "provider": "google",
        "external_email": "user@example.com",
        "granted_scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        "expires_at": now,
        "connected_at": now,
        "message": None,
    }
    responses = {("get", "/oauth-broker/connections/status"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.get_connection_status(uuid.UUID(app_id), "google")

    assert result.status == "connected"
    assert result.provider == "google"
    assert result.external_email == "user@example.com"

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/connections/status"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["provider"] == "google"


def test_disconnect(dummy_client):
    """Test disconnecting from provider."""
    app_id = str(uuid.uuid4())

    responses = {("delete", "/oauth-broker/connections/disconnect"): None}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    service.disconnect(uuid.UUID(app_id), "google")

    method, path, kwargs = client.calls[0]
    assert method == "delete"
    assert path == "/oauth-broker/connections/disconnect"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["provider"] == "google"


# ========== Ephemeral Token Tests ==========


def test_mint_ephemeral_token(dummy_client):
    """Test minting an ephemeral access token."""
    app_id = str(uuid.uuid4())

    response = {
        "access_token": "ya29.abc123",
        "lease_id": "lease-123",
        "expires_in": 3600,
        "broker_lease_expires_in": 300,
        "token_type": "Bearer",
        "granted_scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
    }
    responses = {("post", "/oauth-broker/tokens/mint"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    request = MintTokenRequest(
        app_installation_id=uuid.UUID(app_id),
        tool_id="test-tool",
        provider="google",
        lease_duration=300,
    )
    result = service.mint_ephemeral_token(request)

    assert result.access_token == "ya29.abc123"
    assert result.lease_id == "lease-123"
    assert result.expires_in == 3600
    assert result.broker_lease_expires_in == 300

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/oauth-broker/tokens/mint"
    assert kwargs["json"]["tool_id"] == "test-tool"
    assert kwargs["json"]["lease_duration"] == 300


def test_get_lease_status(dummy_client):
    """Test getting lease status."""
    app_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    response = {
        "lease_id": "lease-123",
        "app_installation_id": app_id,
        "tool_id": "test-tool",
        "provider": "google",
        "granted_scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        "issued_at": now,
        "expires_at": expires,
        "revoked_at": None,
        "is_valid": True,
    }
    responses = {("get", "/oauth-broker/tokens/leases/lease-123"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.get_lease_status("lease-123")

    assert result.lease_id == "lease-123"
    assert result.is_valid is True
    assert result.revoked_at is None

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/tokens/leases/lease-123"


def test_revoke_lease(dummy_client):
    """Test revoking a lease."""
    responses = {("delete", "/oauth-broker/tokens/leases/lease-123"): None}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    service.revoke_lease("lease-123")

    method, path, kwargs = client.calls[0]
    assert method == "delete"
    assert path == "/oauth-broker/tokens/leases/lease-123"


# ========== Proxy Endpoint Tests ==========


def test_gmail_search(dummy_client):
    """Test Gmail search proxy."""
    app_id = str(uuid.uuid4())

    response = {
        "messages": [{"id": "msg1", "threadId": "thread1"}],
        "resultSizeEstimate": 1,
    }
    responses = {("post", "/oauth-broker/proxy/google/gmail/search"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.gmail_search(
        app_id=uuid.UUID(app_id), tool_id="gmail-reader", query="is:unread", max_results=10
    )

    assert "messages" in result
    assert result["resultSizeEstimate"] == 1

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/oauth-broker/proxy/google/gmail/search"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["tool_id"] == "gmail-reader"
    assert kwargs["json"]["query"] == "is:unread"
    assert kwargs["json"]["max_results"] == 10


def test_gmail_get_message(dummy_client):
    """Test Gmail get message proxy."""
    app_id = str(uuid.uuid4())

    response = {"id": "msg1", "threadId": "thread1", "snippet": "Test message"}
    responses = {("post", "/oauth-broker/proxy/google/gmail/getMessage"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.gmail_get_message(
        app_id=uuid.UUID(app_id), tool_id="gmail-reader", message_id="msg1", msg_format="full"
    )

    assert result["id"] == "msg1"

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/oauth-broker/proxy/google/gmail/getMessage"
    assert kwargs["json"]["message_id"] == "msg1"
    assert kwargs["json"]["msg_format"] == "full"


def test_gmail_send(dummy_client):
    """Test Gmail send proxy."""
    app_id = str(uuid.uuid4())

    response = {"id": "msg-sent-1", "threadId": "thread1", "labelIds": ["SENT"]}
    responses = {("post", "/oauth-broker/proxy/google/gmail/send"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.gmail_send(
        app_id=uuid.UUID(app_id),
        tool_id="gmail-sender",
        raw_message="base64encodedmessage",
    )

    assert result["id"] == "msg-sent-1"

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/oauth-broker/proxy/google/gmail/send"
    assert kwargs["json"]["raw_message"] == "base64encodedmessage"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["tool_id"] == "gmail-sender"


def test_gmail_list_labels(dummy_client):
    """Test Gmail list labels proxy."""
    app_id = str(uuid.uuid4())

    response = {
        "labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "SENT", "name": "SENT", "type": "system"},
        ]
    }
    responses = {("get", "/oauth-broker/proxy/google/gmail/labels"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.gmail_list_labels(
        app_id=uuid.UUID(app_id), tool_id="gmail-reader"
    )

    assert "labels" in result
    assert len(result["labels"]) == 2

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/proxy/google/gmail/labels"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["tool_id"] == "gmail-reader"


def test_gmail_modify(dummy_client):
    """Test Gmail modify message proxy."""
    app_id = str(uuid.uuid4())

    response = {"id": "msg1", "threadId": "thread1", "labelIds": ["INBOX"]}
    responses = {("post", "/oauth-broker/proxy/google/gmail/modify"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.gmail_modify(
        app_id=uuid.UUID(app_id),
        tool_id="gmail-reader",
        message_id="msg1",
        remove_labels=["UNREAD"],
    )

    assert result["id"] == "msg1"

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/oauth-broker/proxy/google/gmail/modify"
    assert kwargs["json"]["message_id"] == "msg1"
    assert kwargs["json"]["remove_labels"] == ["UNREAD"]
    assert kwargs["params"]["app_id"] == app_id


def test_drive_list_files(dummy_client):
    """Test Drive list files proxy."""
    app_id = str(uuid.uuid4())

    response = {"files": [{"id": "file1", "name": "test.pdf"}]}
    responses = {("post", "/oauth-broker/proxy/google/drive/listFiles"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.drive_list_files(
        app_id=uuid.UUID(app_id),
        tool_id="drive-reader",
        query="mimeType='application/pdf'",
        page_size=20,
    )

    assert "files" in result
    assert len(result["files"]) == 1

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/oauth-broker/proxy/google/drive/listFiles"
    assert kwargs["json"]["query"] == "mimeType='application/pdf'"
    assert kwargs["json"]["page_size"] == 20


def test_drive_get_file(dummy_client):
    """Test Drive get file metadata proxy."""
    app_id = str(uuid.uuid4())

    response = {"id": "file1", "name": "report.pdf", "mimeType": "application/pdf"}
    responses = {("get", "/oauth-broker/proxy/google/drive/files/file1"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.drive_get_file(
        app_id=uuid.UUID(app_id), tool_id="drive-reader", file_id="file1"
    )

    assert result["id"] == "file1"
    assert result["name"] == "report.pdf"

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/proxy/google/drive/files/file1"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["tool_id"] == "drive-reader"


def test_calendar_list_calendars(dummy_client):
    """Test Calendar list calendars proxy."""
    app_id = str(uuid.uuid4())

    response = {
        "items": [
            {"id": "primary", "summary": "My Calendar"},
            {"id": "holidays", "summary": "Holidays"},
        ]
    }
    responses = {("get", "/oauth-broker/proxy/google/calendar/calendars"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    result = service.calendar_list_calendars(
        app_id=uuid.UUID(app_id), tool_id="calendar-reader"
    )

    assert "items" in result
    assert len(result["items"]) == 2

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/proxy/google/calendar/calendars"
    assert kwargs["params"]["app_id"] == app_id
    assert kwargs["params"]["tool_id"] == "calendar-reader"


def test_calendar_list_events(dummy_client):
    """Test Calendar list events proxy."""
    app_id = str(uuid.uuid4())

    response = {"items": [{"id": "event1", "summary": "Meeting"}]}
    responses = {("get", "/oauth-broker/proxy/google/calendar/events"): response}
    client = dummy_client(responses)
    service = OAuthBrokerService(client)

    time_min = "2026-02-12T00:00:00Z"
    time_max = "2026-02-13T00:00:00Z"

    result = service.calendar_list_events(
        app_id=uuid.UUID(app_id),
        tool_id="calendar-reader",
        calendar_id="primary",
        time_min=time_min,
        time_max=time_max,
        max_results=50,
    )

    assert "items" in result
    assert len(result["items"]) == 1

    method, path, kwargs = client.calls[0]
    assert method == "get"
    assert path == "/oauth-broker/proxy/google/calendar/events"
    assert kwargs["params"]["calendar_id"] == "primary"
    assert kwargs["params"]["time_min"] == time_min
    assert kwargs["params"]["time_max"] == time_max
    assert kwargs["params"]["max_results"] == 50
