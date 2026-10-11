"""
Load the MRT station seed and geocode each station via OneMap.

Seed-based, not API-based: there is no clean public endpoint for MRT station
coordinates, so the station list is version-controlled in seeds/ and enriched
with coordinates at load time.

The `opened` date is carried through so downstream analysis can avoid crediting
a station with affecting prices before it existed.
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

ROOT       = Path(__file__).resolve().parents[2]
SEED       = ROOT / "seeds" / "mrt_stations_north.csv"
TOKEN_URL  = "https://www.onemap.gov.sg/api/auth/post/getToken"
SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"
TARGET     = f"{PROJECT_ID}.raw.mrt_stations"

client = get_client()


def get_token():
    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/json"},
        json={"email": ONEMAP_EMAIL, "password": ONEMAP_PASSWORD},
        timeout=30,
    )
    if r.status_code in (401, 404):
        raise RuntimeError(f"OneMap auth failed ({r.status_code}): {r.text}")
    r.raise_for_status()
    return r.json()["access_token"]


def geocode_station(name, token):
    """Search '<name> MRT STATION' and take the top hit."""
    r = requests.get(
        SEARCH_URL,
        params={"searchVal": f"{name} MRT STATION", "returnGeom": "Y",
                "getAddrDetails": "Y", "pageNum": 1},
        headers={"Authorization": token},
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()

    if payload.get("found", 0) == 0:
        return None

    top = payload["results"][0]
    return {
        "latitude":    float(top["LATITUDE"]),
        "longitude":   float(top["LONGITUDE"]),
        "onemap_name": top.get("SEARCHVAL"),
    }


if __name__ == "__main__":
    print("=" * 55)
    print("MRT Stations — Seed Ingestion")
    print("=" * 55)

    df = pd.read_csv(SEED)
    print(f"Seed contains {len(df)} stations")

    token = get_token()
    print("Geocoding...")

    rows = []
    for r in df.itertuples():
        result = geocode_station(r.station_name, token)
        if result is None:
            print(f"  NOT FOUND: {r.station_name}")
            continue

        rows.append({
            "station_name": r.station_name,
            "line":         r.line,
            "station_code": r.station_code,
            "opened":       pd.to_datetime(r.opened).date(),
            **result,
            "_loaded_at":   datetime.now(ZoneInfo("Asia/Singapore")).replace(tzinfo=None),
        })
        print(f"  {r.station_code} {r.station_name} -> {result['onemap_name']}")
        time.sleep(0.2)

    out = pd.DataFrame(rows)
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE", autodetect=True
    )
    client.load_table_from_dataframe(out, TARGET, job_config=job_config).result()
    print(f"\nLoaded {len(out)} stations to {TARGET}")
