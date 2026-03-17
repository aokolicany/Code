"""Step 2: Complete auth and create the calendar event."""
import json, sys, requests, msal

CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
AUTHORITY = "https://login.microsoftonline.com/organizations"

app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)

try:
    with open(".flow_state.json") as f:
        flow = json.load(f)
except FileNotFoundError:
    print("Run step1_get_auth_url.py first.")
    sys.exit(1)

result = app.acquire_token_by_device_flow(flow)

if "access_token" not in result:
    print(f"Auth failed: {result.get('error_description', result)}")
    sys.exit(1)

print("Authenticated. Creating event...")

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
    print(f"\nEvent created!")
    print(f"  Subject : {ev['subject']}")
    print(f"  Start   : {ev['start']['dateTime']}")
    print(f"  End     : {ev['end']['dateTime']}")
    print(f"  Link    : {ev.get('webLink', 'N/A')}")
    import os; os.remove(".flow_state.json")
else:
    print(f"Failed ({resp.status_code}): {resp.text}")
    sys.exit(1)
