"""Proxy mixins for Gmail, Drive, and Calendar operations.

Proxy endpoints follow a consistent pattern: methods with request bodies
(search, getMessage, send, modify, listFiles) use POST, while read-only
methods without bodies (labels, file metadata, calendars, events) use GET.
This mirrors the server-side routing design.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from ...schemas.oauth_broker import (
    DriveListFilesRequest,
    GmailGetMessageRequest,
    GmailModifyRequest,
    GmailSearchRequest,
    GmailSendRequest,
)


class ProxyMixin:
    """Mixin for Gmail, Drive, and Calendar proxy endpoints (Mode 1)."""

    client: Any  # Provided by BaseService when mixed in

    # ========== Gmail Proxy Endpoints ==========

    def gmail_search(
        self, app_id: UUID, tool_id: str, query: str, max_results: int = 10
    ) -> dict[str, Any]:
        """
        Proxy Gmail search request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            query: Gmail search query (e.g., "is:unread", "from:user@example.com")
            max_results: Maximum number of results

        Returns:
            Gmail API response

        Example:
            >>> results = client.oauth_broker.gmail_search(
            ...     app_id=app_id,
            ...     tool_id="gmail-reader",
            ...     query="is:unread subject:report",
            ...     max_results=20
            ... )
        """
        request = GmailSearchRequest(query=query, max_results=max_results)
        response = self.client.post(
            "/oauth-broker/proxy/google/gmail/search",
            json=request.model_dump(),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def gmail_get_message(
        self, app_id: UUID, tool_id: str, message_id: str, msg_format: Literal["full", "metadata", "minimal", "raw"] = "full"
    ) -> dict[str, Any]:
        """
        Proxy Gmail get message request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            message_id: Gmail message ID
            msg_format: Message format (full, metadata, minimal, raw)

        Returns:
            Gmail API response
        """
        request = GmailGetMessageRequest(message_id=message_id, msg_format=msg_format)
        response = self.client.post(
            "/oauth-broker/proxy/google/gmail/getMessage",
            json=request.model_dump(),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def gmail_send(
        self, app_id: UUID, tool_id: str, raw_message: str
    ) -> dict[str, Any]:
        """
        Proxy Gmail send request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            raw_message: Base64url encoded RFC 2822 message

        Returns:
            Gmail API response
        """
        request = GmailSendRequest(raw_message=raw_message)
        response = self.client.post(
            "/oauth-broker/proxy/google/gmail/send",
            json=request.model_dump(),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def gmail_list_labels(self, app_id: UUID, tool_id: str) -> dict[str, Any]:
        """
        Proxy Gmail list labels request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier

        Returns:
            Gmail API response
        """
        response = self.client.get(
            "/oauth-broker/proxy/google/gmail/labels",
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def gmail_modify(
        self,
        app_id: UUID,
        tool_id: str,
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Proxy Gmail modify message request.

        Used for operations like:
        - Mark as read: remove_labels=["UNREAD"]
        - Mark as unread: add_labels=["UNREAD"]
        - Delete (move to trash): add_labels=["TRASH"], remove_labels=["INBOX"]
        - Archive: remove_labels=["INBOX"]

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            message_id: Gmail message ID
            add_labels: Labels to add
            remove_labels: Labels to remove

        Returns:
            Gmail API response
        """
        request = GmailModifyRequest(
            message_id=message_id, add_labels=add_labels, remove_labels=remove_labels
        )
        response = self.client.post(
            "/oauth-broker/proxy/google/gmail/modify",
            json=request.model_dump(exclude_none=True),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    # ========== Google Drive Proxy Endpoints ==========

    def drive_list_files(
        self, app_id: UUID, tool_id: str, query: str | None = None, page_size: int = 10
    ) -> dict[str, Any]:
        """
        Proxy Google Drive list files request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            query: Drive query (e.g., "name contains 'report'")
            page_size: Maximum number of files

        Returns:
            Drive API response

        Example:
            >>> files = client.oauth_broker.drive_list_files(
            ...     app_id=app_id,
            ...     tool_id="drive-reader",
            ...     query="mimeType='application/pdf'",
            ...     page_size=20
            ... )
        """
        request = DriveListFilesRequest(query=query, page_size=page_size)
        response = self.client.post(
            "/oauth-broker/proxy/google/drive/listFiles",
            json=request.model_dump(exclude_none=True),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def drive_get_file(
        self, app_id: UUID, tool_id: str, file_id: str
    ) -> dict[str, Any]:
        """
        Proxy Google Drive get file metadata request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            file_id: Drive file ID

        Returns:
            Drive API response
        """
        response = self.client.get(
            f"/oauth-broker/proxy/google/drive/files/{file_id}",
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    # ========== Google Calendar Proxy Endpoints ==========

    def calendar_list_calendars(self, app_id: UUID, tool_id: str) -> dict[str, Any]:
        """
        Proxy Google Calendar list calendars request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier

        Returns:
            Calendar API response
        """
        response = self.client.get(
            "/oauth-broker/proxy/google/calendar/calendars",
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def calendar_list_events(
        self,
        app_id: UUID,
        tool_id: str,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """
        Proxy Google Calendar list events request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            calendar_id: Calendar ID (default: "primary")
            time_min: RFC3339 timestamp for start time
            time_max: RFC3339 timestamp for end time
            max_results: Maximum number of events

        Returns:
            Calendar API response

        Example:
            >>> events = client.oauth_broker.calendar_list_events(
            ...     app_id=app_id,
            ...     tool_id="calendar-reader",
            ...     time_min="2026-02-12T00:00:00Z",
            ...     time_max="2026-02-13T00:00:00Z",
            ...     max_results=50
            ... )
        """
        params = {
            "app_id": str(app_id),
            "tool_id": tool_id,
            "calendar_id": calendar_id,
            "max_results": max_results,
        }
        if time_min is not None:
            params["time_min"] = time_min
        if time_max is not None:
            params["time_max"] = time_max

        response = self.client.get(
            "/oauth-broker/proxy/google/calendar/events", params=params
        )
        return response
