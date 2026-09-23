"""Verify OneMap credentials and inspect token expiry."""

import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from config import ONEMAP_EMAIL, ONEMAP_PASSWORD

TOKEN_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"

if not ONEMAP_EMAIL or not ONEMAP_PASSWORD:
    raise SystemExit("ONEMAP_EMAIL / ONEMAP_PASSWORD missing from .env")

print(f"Authenticating as {ONEMAP_EMAIL}...")

r = requests.post(
    TOKEN_URL,
    headers={"Content-Type": "application/json"},
    json={"email": ONEMAP_EMAIL, "password": ONEMAP_PASSWORD},
    timeout=30,
)

if r.status_code == 401:
    raise SystemExit("401 — authentication failed. Check the password.")
if r.status_code == 404:
    raise SystemExit(f"404 — {r.text}\nIs the account registered and confirmed?")
r.raise_for_status()

payload = r.json()
token   = payload["access_token"]
expiry  = datetime.fromtimestamp(int(payload["expiry_timestamp"]),
                                 ZoneInfo("Asia/Singapore"))

print(f"  Token acquired: {token[:12]}...{token[-6:]}")
print(f"  Expires: {expiry:%Y-%m-%d %H:%M:%S} SGT")
