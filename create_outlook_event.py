"""
Create an Outlook calendar event via Microsoft Graph API.

Requirements:
    pip install requests msal

Setup:
    1. Register an app in Azure AD (https://portal.azure.com)
    2. Grant Calendar.ReadWrite permission
    3. Set environment variables:
       AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID
"""

import os
import requests
import msal

CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
TENANT_ID = os.environ["AZURE_TENANT_ID"]

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = ["https://graph.microsoft.com/.default"]
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0/me/events"


def get_access_token() -> str:
    app = msal.ConfidentialClientApplication(
        CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET
    )
    result = app.acquire_token_for_client(scopes=SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire token: {result.get('error_description')}")
    return result["access_token"]


def create_event(token: str) -> dict:
    event = {
        "subject": "Meeting",
        "start": {
            "dateTime": "2026-03-17T17:30:00",
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": "2026-03-17T18:00:00",
            "timeZone": "UTC",
        },
        "isAllDay": False,
    }

    response = requests.post(
        GRAPH_ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=event,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    token = get_access_token()
    event = create_event(token)
    print(f"Event created: {event['id']}")
    print(f"Subject: {event['subject']}")
    print(f"Start: {event['start']['dateTime']}")
    print(f"End: {event['end']['dateTime']}")
