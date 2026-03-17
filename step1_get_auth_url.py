"""Step 1: Get device auth URL and save flow state."""
import json, sys, msal

CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
AUTHORITY = "https://login.microsoftonline.com/organizations"
SCOPES = ["https://graph.microsoft.com/Calendars.ReadWrite"]

app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
flow = app.initiate_device_flow(scopes=SCOPES)

if "user_code" not in flow:
    print(f"Error: {flow}")
    sys.exit(1)

with open(".flow_state.json", "w") as f:
    json.dump(flow, f)

print("\n" + "="*60)
print(f"  1. Go to : {flow['verification_uri']}")
print(f"  2. Enter : {flow['user_code']}")
print("="*60)
print("\nOnce authenticated, run: python step2_create_event.py")
