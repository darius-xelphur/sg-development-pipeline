"""
Shared OneMap auth and search, used by every script that geocodes.

Auth: OneMap tokens last 3 days and don't auto-renew, so callers fetch a fresh
one per run. The search endpoint appears to work unauthenticated, but we send
the token anyway — it fails loudly up front if credentials break.
"""

import sys
import time
import requests
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import ONEMAP_EMAIL, ONEMAP_PASSWORD

TOKEN_URL  = "https://www.onemap.gov.sg/api/auth/post/getToken"
SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"


def get_token():
    if not ONEMAP_EMAIL or not ONEMAP_PASSWORD:
        raise RuntimeError("OneMap credentials missing")

    print("Authenticating with OneMap...")
    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/json"},
        json={"email": ONEMAP_EMAIL, "password": ONEMAP_PASSWORD},
        timeout=30,
    )
    if r.status_code in (400, 401, 404):
        raise RuntimeError(f"OneMap auth failed ({r.status_code}): {r.text}")
    r.raise_for_status()

    payload = r.json()
    expiry  = datetime.fromtimestamp(int(payload["expiry_timestamp"]),
                                     ZoneInfo("Asia/Singapore"))
    print(f"  Token acquired, expires {expiry:%Y-%m-%d %H:%M} SGT")
    return payload["access_token"]


def search(search_val, token, attempts=3, backoff=5):
    """
    Return (found, results): the total hit count and the first page of hits.
    Retries transient failures; raises once attempts are exhausted.
    """
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(
                SEARCH_URL,
                params={"searchVal": search_val, "returnGeom": "Y",
                        "getAddrDetails": "Y", "pageNum": 1},
                headers={"Authorization": token},
                timeout=30,
            )
            r.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == attempts:
                raise
            print(f"    retry {attempt}/{attempts}: {e}")
            time.sleep(backoff)

    payload = r.json()
    found   = payload.get("found", 0)
    return found, (payload["results"] if found else [])
