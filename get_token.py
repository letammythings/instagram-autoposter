import os
import requests
import config  # noqa: F401 — loads `.env` and `~/.env.master` via config.py

app_id = os.getenv("INSTAGRAM_APP_ID")
app_secret = os.getenv("INSTAGRAM_APP_SECRET")
short_lived_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")

if not app_id or not app_secret or not short_lived_token:
    raise ValueError("INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET, and INSTAGRAM_ACCESS_TOKEN must be set (e.g. in .env or ~/.env.master)")

response = requests.get(
    "https://graph.facebook.com/v21.0/oauth/access_token",
    params={
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_lived_token,
    },
)

print(response.json())

data = response.json()
if "access_token" in data:
    print(f"\nLong-lived token:\n{data['access_token']}")
    print(f"\nExpires in: {data.get('expires_in', 'unknown')} seconds (~60 days)")
    print("\nCopy this token into INSTAGRAM_ACCESS_TOKEN in your .env")
else:
    print("\nFailed to exchange token.")
