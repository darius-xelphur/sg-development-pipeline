"""
Load the MRT station seed and geocode each station via OneMap.

Seed-based, not API-based: there is no clean public endpoint for MRT station
coordinates, so the station list is version-controlled in seeds/ and enriched
with coordinates at load time.

The `opened` date is carried through so downstream analysis can avoid crediting
a station with affecting prices before it existed.

Every seed row must geocode to a result named exactly '<NAME> MRT STATION'.
Anything less fails the run rather than truncating the table with a station
silently missing or misplaced.
"""

import sys
import re
import time
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import get_client, PROJECT_ID
from ingestion.onemap.client import get_token, search

ROOT       = Path(__file__).resolve().parents[2]
SEED       = ROOT / "seeds" / "mrt_stations_north.csv"
TARGET     = f"{PROJECT_ID}.raw.mrt_stations"

client = get_client()


def geocode_station(name, token):
    """
    Search '<name> MRT STATION' and take the first hit that is that station.

    Fuzzy search ranks exits, bus interchanges and similarly named stations
    (e.g. '<NAME> SOUTH') alongside the real one, so the top hit alone can't
    be trusted. Accept '<NAME> MRT STATION' optionally followed by a code
    suffix such as ' (NS9 / TE2)'.
    """
    wanted = re.compile(rf"^{re.escape(name.upper())} MRT STATION( \(.*\))?$")
    _, results = search(f"{name} MRT STATION", token)

    for hit in results:
        if wanted.match(hit.get("SEARCHVAL", "")):
            return {
                "latitude":    float(hit["LATITUDE"]),
                "longitude":   float(hit["LONGITUDE"]),
                "onemap_name": hit["SEARCHVAL"],
            }
    return None


if __name__ == "__main__":
    print("=" * 55)
    print("MRT Stations — Seed Ingestion")
    print("=" * 55)

    df = pd.read_csv(SEED)
    print(f"Seed contains {len(df)} stations")

    token = get_token()
    print("Geocoding...")

    rows, missing = [], []
    for r in df.itertuples():
        result = geocode_station(r.station_name, token)
        if result is None:
            print(f"  NO MATCH: {r.station_code} {r.station_name}")
            missing.append(r.station_code)
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

    if missing:
        raise SystemExit(f"{len(missing)} seed station(s) not matched in OneMap: "
                         f"{', '.join(missing)} — {TARGET} left unchanged")

    out = pd.DataFrame(rows)
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE", autodetect=True
    )
    client.load_table_from_dataframe(out, TARGET, job_config=job_config).result()
    print(f"\nLoaded {len(out)} stations to {TARGET}")
