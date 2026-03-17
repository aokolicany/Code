"""
Creates an Outlook calendar event for today at 5:30 PM (30 min).
Uses device code flow: opens a browser URL to authenticate, then creates the event.
Run: python create_event_now.py
"""

import sys
import requests
import msal

CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"  # Microsoft Office public client
AUTHORITY = "https://login.microsoftonline.com/organizations"
SCOPES = ["https://graph.microsoft.com/Calendars.ReadWrite"]

app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)

flow = app.initiate_device_flow(scopes=SCOPES)
if "user_code" not in flow:
    print(f"Failed to create device flow: {flow}")
    sys.exit(1)

print("\n" + "="*60)
print(f"  Visit : {flow['verification_uri']}")
print(f"  Code  : {flow['user_code']}")
print("="*60)
print("Enter the code above on the website, then press Enter here...")
input()

result = app.acquire_token_by_device_flow(flow)

if "access_token" not in result:
    print(f"Auth failed: {result.get('error_description', result)}")
    sys.exit(1)

print("Authenticated successfully. Creating event...")

event = {
    "subject": "Meeting",
    "start": {"dateTime": "2026-03-17T17:30:00", "timeZone": "UTC"},
    "end":   {"dateTime": "2026-03-17T18:00:00", "timeZone": "UTC"},
}

resp = requests.post(
    "https://graph.microsoft.com/v1.0/me/events",
    headers={
        "Authorization": f"Bearer {result['access_token']}",
        "Content-Type": "application/json",
    },
    json=event,
)

if resp.status_code == 201:
    ev = resp.json()
    print(f"\nEvent created successfully!")
    print(f"  Subject : {ev['subject']}")
    print(f"  Start   : {ev['start']['dateTime']}")
    print(f"  End     : {ev['end']['dateTime']}")
    print(f"  Link    : {ev.get('webLink', 'N/A')}")
else:
    print(f"Failed to create event ({resp.status_code}): {resp.text}")
    sys.exit(1)
