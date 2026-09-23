"""
Geocode distinct Woodlands HDB addresses via OneMap into raw.address_geocodes.

INCREMENTAL BY DESIGN — unlike the other loads in this pipeline, which are full
dumps. Each address costs an external API call against a free government
service, and addresses are immutable, so we geocode only what isn't cached.

Failed lookups are stored with status='NOT_FOUND' so they aren't retried every
run. Delete those rows to force a retry.

Auth: OneMap tokens last 3 days and don't auto-renew, so we fetch a fresh one
per run. The search endpoint appears to work unauthenticated, but we send the
token anyway — it fails loudly up front if credentials break, and the routing
and theme endpoints will need it later.
"""

import sys
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import get_client, PROJECT_ID, ONEMAP_EMAIL, ONEMAP_PASSWORD

TOKEN_URL  = "https://www.onemap.gov.sg/api/auth/post/getToken"
SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"
TARGET     = f"{PROJECT_ID}.raw.address_geocodes"
SLEEP      = 0.2

client = get_client()


def get_token():
    print("Authenticating with OneMap...")
    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/json"},
        json={"email": ONEMAP_EMAIL, "password": ONEMAP_PASSWORD},
        timeout=30,
    )
    if r.status_code in (401, 404):
        raise RuntimeError(f"OneMap auth failed ({r.status_code}): {r.text}")
    r.raise_for_status()

    payload = r.json()
    expiry  = datetime.fromtimestamp(int(payload["expiry_timestamp"]),
                                     ZoneInfo("Asia/Singapore"))
    print(f"  Token acquired, expires {expiry:%Y-%m-%d %H:%M} SGT")
    return payload["access_token"]


def ensure_table():
    client.query(f"""
        CREATE TABLE IF NOT EXISTS `{TARGET}` (
          address        STRING   NOT NULL,
          block          STRING,
          street_name    STRING,
          postal_code    STRING,
          latitude       FLOAT64,
          longitude      FLOAT64,
          onemap_name    STRING,
          matched_block  STRING,
          matched_street STRING,
          found_count    INT64,
          status         STRING   NOT NULL,
          geocoded_at    DATETIME NOT NULL
        )
    """).result()


def addresses_to_geocode():
    """Distinct Woodlands addresses not already cached."""
    sql = f"""
        SELECT DISTINCT
          s.block,
          s.street_name,
          CONCAT(s.block, ' ', s.street_name) AS address
        FROM `{PROJECT_ID}.staging.hdb_resale_transactions` s
        LEFT JOIN `{TARGET}` g
          ON CONCAT(s.block, ' ', s.street_name) = g.address
        WHERE s.valid = 1
          AND s.town = 'WOODLANDS'
          AND g.address IS NULL
    """
    df = client.query(sql).to_dataframe(create_bqstorage_client=False)
    print(f"  {len(df):,} addresses need geocoding")
    return df


def geocode(address, token, attempts=3, backoff=5):
    """Look up one address. Retries transient failures before giving up."""
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(
                SEARCH_URL,
                params={"searchVal": address, "returnGeom": "Y",
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

    if found == 0:
        return {"status": "NOT_FOUND", "found_count": 0}

    top = payload["results"][0]
    return {
        "status":         "OK",
        "found_count":    found,
        "postal_code":    top.get("POSTAL"),
        "latitude":       float(top["LATITUDE"]),
        "longitude":      float(top["LONGITUDE"]),
        "onemap_name":    top.get("SEARCHVAL"),
        "matched_block":  top.get("BLK_NO"),
        "matched_street": top.get("ROAD_NAME"),
    }


def load(rows):
    if not rows:
        print("Nothing to load.")
        return
    df = pd.DataFrame(rows)
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(df, TARGET, job_config=job_config).result()
    print(f"  Appended {len(df):,} rows to {TARGET}")


if __name__ == "__main__":
    print("=" * 55)
    print("OneMap Address Geocoding — Woodlands")
    print("=" * 55)

    ensure_table()
    token = get_token()

    print("Finding uncached addresses...")
    pending = addresses_to_geocode()

    if pending.empty:
        print("\nAll addresses already geocoded. Nothing to do.")
        sys.exit(0)

    now      = datetime.now(ZoneInfo("Asia/Singapore")).replace(tzinfo=None)
    rows     = []
    failures = 0

    print("Geocoding...")
    for i, r in enumerate(pending.itertuples(), start=1):
        try:
            result = geocode(r.address, token)
        except Exception as e:
            print(f"  [{i}/{len(pending)}] {r.address} — gave up: {e}")
            failures += 1
            continue

        if result["status"] == "NOT_FOUND":
            failures += 1

        rows.append({
            "address":        r.address,
            "block":          r.block,
            "street_name":    r.street_name,
            "postal_code":    result.get("postal_code"),
            "latitude":       result.get("latitude"),
            "longitude":      result.get("longitude"),
            "onemap_name":    result.get("onemap_name"),
            "matched_block":  result.get("matched_block"),
            "matched_street": result.get("matched_street"),
            "found_count":    result.get("found_count"),
            "status":         result["status"],
            "geocoded_at":    now,
        })

        if i % 50 == 0:
            print(f"  [{i}/{len(pending)}] processed")

        time.sleep(SLEEP)

    print(f"Resolved {len(rows) - failures:,} / {len(pending):,} "
          f"({failures} not found or errored)")
    load(rows)

    print("\nGeocoding complete.")
