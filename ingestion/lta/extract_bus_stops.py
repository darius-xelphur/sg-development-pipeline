"""
Ingest all Singapore bus stops from LTA DataMall into raw.lta_bus_stops.

Full dump — ~5,000 stops across ~10 paginated calls. Cheap enough to refresh
every run, unlike the OneMap geocoding which is per-address and cached.

Pagination: DataMall caps responses at 500 records. Walk $skip in 500s until
a page returns fewer than 500 rows.

Auth: AccountKey header on every request.
"""

import sys
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import get_client, PROJECT_ID, LTA_ACCOUNT_KEY

URL       = "https://datamall2.mytransport.sg/ltaodataservice/BusStops"
TARGET    = f"{PROJECT_ID}.raw.lta_bus_stops"
PAGE_SIZE = 500

client = get_client()


def fetch_all():
    """Page through $skip until a short page signals the end."""
    if not LTA_ACCOUNT_KEY:
        raise SystemExit("LTA_ACCOUNT_KEY missing from .env")

    headers = {"AccountKey": LTA_ACCOUNT_KEY}
    records, skip = [], 0

    print("Fetching bus stops from LTA DataMall...")
    while True:
        r = requests.get(URL, headers=headers,
                         params={"$skip": skip}, timeout=30)
        if r.status_code in (401, 403):
            raise RuntimeError(f"Auth failed ({r.status_code}) — check AccountKey")
        r.raise_for_status()

        page = r.json().get("value", [])
        records.extend(page)
        print(f"  $skip={skip}: {len(page)} records (total {len(records):,})")

        if len(page) < PAGE_SIZE:
            break
        skip += PAGE_SIZE

    return records


def validate(df):
    print("Validating...")
    if df.empty:
        raise ValueError("No records returned — aborting")

    required = ["BusStopCode", "Latitude", "Longitude"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    # DataMall returns 0.0 coordinates for a handful of decommissioned stops
    zero_coords = ((df["Latitude"] == 0) | (df["Longitude"] == 0)).sum()
    if zero_coords:
        print(f"  Warning: {zero_coords} stops with zero coordinates")

    dupes = df["BusStopCode"].duplicated().sum()
    if dupes:
        print(f"  Warning: {dupes} duplicate bus stop codes")

    print(f"  Passed — {len(df):,} stops")
    return df


def load(df):
    print(f"Loading to {TARGET}...")
    df = df.rename(columns={
        "BusStopCode": "bus_stop_code",
        "RoadName":    "road_name",
        "Description": "description",
        "Latitude":    "latitude",
        "Longitude":   "longitude",
    })
    df["_loaded_at"] = datetime.now(ZoneInfo("Asia/Singapore")).replace(tzinfo=None)

    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        autodetect=True,
    )
    client.load_table_from_dataframe(df, TARGET, job_config=job_config).result()
    print(f"  Loaded {client.get_table(TARGET).num_rows:,} rows")


if __name__ == "__main__":
    print("=" * 55)
    print("LTA Bus Stops — Raw Ingestion")
    print("=" * 55)

    df = pd.DataFrame(fetch_all())
    df = validate(df)
    load(df)

    print("\nIngestion complete.")
