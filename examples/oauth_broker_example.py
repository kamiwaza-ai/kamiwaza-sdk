#!/usr/bin/env python3
"""
OAuth Broker Example

This example demonstrates how to use the Kamiwaza OAuth Broker to:
1. Create an app installation
2. Start OAuth flow for Google
3. Check connection status
4. Use proxy endpoints (Gmail, Drive, Calendar)
5. Mint ephemeral tokens (advanced)
6. Manage tool policies

Prerequisites:
- Kamiwaza instance running with OAuth Broker configured
- Google OAuth credentials configured
- Valid API key or authentication credentials
"""

import os
import sys
from datetime import datetime, timedelta

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.oauth_broker import (
    AppInstallationCreate,
    MintTokenRequest,
    ToolPolicyCreate,
)


def main():
    # Initialize client
    base_url = os.environ.get("KAMIWAZA_BASE_URL", "https://localhost/api")
    api_key = os.environ.get("KAMIWAZA_API_KEY")

    if not api_key:
        print("Error: KAMIWAZA_API_KEY environment variable required")
        sys.exit(1)

    client = KamiwazaClient(base_url, api_key=api_key)

    print("=== OAuth Broker Example ===\n")

    # Step 1: Create an app installation
    print("1. Creating app installation...")
    try:
        app = client.oauth_broker.create_app_installation(
            AppInstallationCreate(
                name="Email Assistant Demo",
                description="Example app for OAuth broker integration",
                allowed_tools=["gmail-reader", "gmail-sender", "drive-reader"],
            )
        )
        print(f"   Created app: {app.name} (ID: {app.id})")
    except APIError as e:
        print(f"   Error: {e}")
        sys.exit(1)

    try:
        # Step 2: Start OAuth flow
        print("\n2. Starting Google OAuth flow...")
        scopes = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
        ]

        auth_result = client.oauth_broker.start_google_auth(app.id, scopes)
        print(f"   Authorization URL: {auth_result.auth_url}")
        print(f"   State: {auth_result.state}")
        print("\n   ** Visit the URL above to authorize **")
        print("   ** After authorization, check connection status **\n")

        # Step 3: Check connection status
        print("3. Checking connection status...")
        status = client.oauth_broker.get_connection_status(app.id, "google")
        print(f"   Status: {status.status}")

        if status.status == "connected":
            print(f"   Connected as: {status.external_email}")
            print(f"   Granted scopes: {', '.join(status.granted_scopes or [])}")

            # Step 4: Create tool policy
            print("\n4. Creating tool policy...")
            policy = client.oauth_broker.create_tool_policy(
                ToolPolicyCreate(
                    app_installation_id=app.id,
                    tool_id="gmail-reader",
                    provider="google",
                    allowed_operations=[
                        "gmail.search",
                        "gmail.getMessage",
                        "gmail.labels.list",
                    ],
                    allowed_scope_subset=[
                        "https://www.googleapis.com/auth/gmail.readonly"
                    ],
                )
            )
            print(f"   Created policy for tool: {policy.tool_id}")

            # Step 5: Use Gmail proxy endpoints
            print("\n5. Using Gmail proxy endpoints...")

            # List labels
            print("   5a. Listing Gmail labels...")
            labels = client.oauth_broker.gmail_list_labels(app.id, "gmail-reader")
            print(f"       Found {len(labels.get('labels', []))} labels")

            # Search emails
            print("   5b. Searching for unread emails...")
            results = client.oauth_broker.gmail_search(
                app_id=app.id, tool_id="gmail-reader", query="is:unread", max_results=5
            )
            message_count = results.get("resultSizeEstimate", 0)
            print(f"       Found ~{message_count} unread messages")

            # Step 6: Use Drive proxy endpoints
            print("\n6. Using Drive proxy endpoints...")
            files = client.oauth_broker.drive_list_files(
                app_id=app.id,
                tool_id="drive-reader",
                query="mimeType='application/pdf'",
                page_size=5,
            )
            file_count = len(files.get("files", []))
            print(f"   Found {file_count} PDF files")

            # Step 7: Use Calendar proxy endpoints
            print("\n7. Using Calendar proxy endpoints...")

            # List calendars
            calendars = client.oauth_broker.calendar_list_calendars(
                app.id, "calendar-reader"
            )
            calendar_count = len(calendars.get("items", []))
            print(f"   Found {calendar_count} calendars")

            # List upcoming events
            now = datetime.utcnow()
            time_min = now.isoformat() + "Z"
            time_max = (now + timedelta(days=7)).isoformat() + "Z"

            events = client.oauth_broker.calendar_list_events(
                app_id=app.id,
                tool_id="calendar-reader",
                calendar_id="primary",
                time_min=time_min,
                time_max=time_max,
                max_results=10,
            )
            event_count = len(events.get("items", []))
            print(f"   Found {event_count} upcoming events (next 7 days)")

            # Step 8: Mint ephemeral token (advanced mode)
            print("\n8. Minting ephemeral token (Mode 2 - Advanced)...")
            print("   WARNING: Only use in high-security environments!")

            mint_request = MintTokenRequest(
                app_installation_id=app.id,
                tool_id="gmail-reader",
                provider="google",
                scope_subset=["https://www.googleapis.com/auth/gmail.readonly"],
                lease_duration=300,  # 5 minutes
            )

            token = client.oauth_broker.mint_ephemeral_token(mint_request)
            print(f"   Minted token with lease ID: {token.lease_id}")
            print(f"   Token expires in: {token.expires_in} seconds")
            print(f"   Broker lease expires in: {token.broker_lease_expires_in} seconds")

            # Check lease status
            lease = client.oauth_broker.get_lease_status(token.lease_id)
            print(f"   Lease is valid: {lease.is_valid}")

            # Revoke the lease
            print("   Revoking lease...")
            client.oauth_broker.revoke_lease(token.lease_id)
            print("   Lease revoked")

        elif status.status == "needs_reauth":
            print("   Connection needs reauthorization")
            print("   Run the OAuth flow again to reconnect")
        else:
            print("   Not connected. Complete the OAuth flow first.")

        # Step 9: List app installations
        print("\n9. Listing app installations...")
        apps = client.oauth_broker.list_app_installations()
        print(f"   Total apps: {apps.total}")
        for a in apps.items[:3]:  # Show first 3
            print(f"   - {a.name} ({a.lifecycle_status})")

        # Step 10: List tool policies
        print("\n10. Listing tool policies...")
        policies = client.oauth_broker.list_tool_policies(app_id=app.id)
        print(f"    Total policies: {policies.total}")
        for p in policies.items:
            print(
                f"    - Tool: {p.tool_id}, Operations: {', '.join(p.allowed_operations[:3])}"
            )

    finally:
        # Cleanup
        print("\n=== Cleanup ===")
        print("Deleting app installation...")
        try:
            client.oauth_broker.delete_app_installation(app.id)
            print("Cleanup complete")
        except APIError as e:
            print(f"Cleanup error: {e}")

    print("\n=== Example Complete ===")


if __name__ == "__main__":
    main()
