"""
Ingest MOE school directory into raw.schools, then geocode postal codes
into raw.school_geocodes.

Two tables, two load patterns:
  raw.schools         — full dump; attributes change (principals, phone numbers)
  raw.school_geocodes — incremental cache keyed on postal_code; a school
                        building does not move, and each lookup costs an
                        OneMap call.

The source has postal codes but no coordinates. Singapore's 6-digit postal
codes identify individual buildings, so they geocode reliably.

Only definitive answers (OK / NOT_FOUND) are cached. A lookup that errors after
retries is skipped, so it is attempted again on the next run.
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
from config import get_client, PROJECT_ID
from ingestion.onemap.client import get_token, search

DATASET_ID  = "d_688b934f82c1059ed0a6993d2a829089"
SEARCH_API  = "https://data.gov.sg/api/action/datastore_search"

SCHOOLS     = f"{PROJECT_ID}.raw.schools"
GEOCODES    = f"{PROJECT_ID}.raw.school_geocodes"
PAGE_SIZE   = 100

client = get_client()


# ── Schools ───────────────────────────────────────────────────────────────────
def fetch_schools():
    records, offset = [], 0
    print("Fetching school directory...")

    while True:
        r = requests.get(SEARCH_API,
                         params={"resource_id": DATASET_ID,
                                 "limit": PAGE_SIZE, "offset": offset},
                         timeout=30)
        r.raise_for_status()
        page = r.json()["result"]["records"]
        if not page:
            break

        records.extend(page)
        print(f"  offset={offset}: {len(page)} records (total {len(records)})")
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return records


def load_schools(df):
    print(f"Loading to {SCHOOLS}...")
    df["_loaded_at"] = datetime.now(ZoneInfo("Asia/Singapore")).replace(tzinfo=None)
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE", autodetect=True
    )
    client.load_table_from_dataframe(df, SCHOOLS, job_config=job_config).result()
    print(f"  Loaded {client.get_table(SCHOOLS).num_rows:,} schools")


# ── Geocoding ─────────────────────────────────────────────────────────────────
def ensure_geocode_table():
    client.query(f"""
        CREATE TABLE IF NOT EXISTS `{GEOCODES}` (
          postal_code  STRING   NOT NULL,
          latitude     FLOAT64,
          longitude    FLOAT64,
          onemap_name  STRING,
          status       STRING   NOT NULL,
          geocoded_at  DATETIME NOT NULL
        )
    """).result()


def postcodes_to_geocode():
    sql = f"""
        SELECT DISTINCT s.postal_code
        FROM `{SCHOOLS}` s
        LEFT JOIN `{GEOCODES}` g
          ON s.postal_code = g.postal_code
         AND g.status IN ('OK', 'NOT_FOUND')  -- legacy 'ERROR' rows get retried
        WHERE g.postal_code IS NULL
          AND s.postal_code IS NOT NULL
    """
    df = client.query(sql).to_dataframe(create_bqstorage_client=False)
    print(f"  {len(df)} postal codes need geocoding")
    return df


def geocode_postcode(postal_code, token):
    _, results = search(postal_code, token)
    if not results:
        return {"status": "NOT_FOUND"}

    top = results[0]
    return {
        "status":      "OK",
        "latitude":    float(top["LATITUDE"]),
        "longitude":   float(top["LONGITUDE"]),
        "onemap_name": top.get("SEARCHVAL"),
    }


if __name__ == "__main__":
    print("=" * 55)
    print("MOE Schools — Ingestion and Geocoding")
    print("=" * 55)

    df = pd.DataFrame(fetch_schools())
    if df.empty:
        raise SystemExit("No school records returned — aborting")
    load_schools(df)

    print("\nGeocoding postal codes...")
    ensure_geocode_table()
    pending = postcodes_to_geocode()

    if pending.empty:
        print("All postal codes already geocoded.")
        raise SystemExit(0)

    token = get_token()
    now   = datetime.now(ZoneInfo("Asia/Singapore")).replace(tzinfo=None)
    rows, failures = [], 0

    for i, r in enumerate(pending.itertuples(), start=1):
        try:
            result = geocode_postcode(r.postal_code, token)
        except Exception as e:
            # Not cached: a transient failure must not become a permanent miss
            print(f"  {r.postal_code} — gave up, will retry next run: {e}")
            failures += 1
            continue

        if result["status"] != "OK":
            failures += 1

        rows.append({
            "postal_code": r.postal_code,
            "latitude":    result.get("latitude"),
            "longitude":   result.get("longitude"),
            "onemap_name": result.get("onemap_name"),
            "status":      result["status"],
            "geocoded_at": now,
        })

        if i % 50 == 0:
            print(f"  [{i}/{len(pending)}] processed")
        time.sleep(0.2)

    if rows:
        client.load_table_from_dataframe(
            pd.DataFrame(rows), GEOCODES,
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
        ).result()

    print(f"\nGeocoded {len(pending) - failures} / {len(pending)} "
          f"({failures} not found or errored)")
