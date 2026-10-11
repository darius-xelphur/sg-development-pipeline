"""
Load the MRT station seed and geocode each station via OneMap.

Seed-based, not API-based: there is no clean public endpoint for MRT station
coordinates, so the station list is version-controlled in seeds/ and enriched
with coordinates at load time.

The `opened` date is carried through so downstream analysis can avoid crediting
a station with affecting prices before it existed.

Stations not yet open — `opened` is TBA or in the future — are skipped, so
planned stations can sit in the seed and are picked up once dated.

Every open seed row must geocode to a result named exactly '<NAME> MRT STATION'.
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
SEED       = ROOT / "seeds" / "mrt_stations.csv"
TARGET     = f"{PROJECT_ID}.raw.mrt_stations"

client = get_client()


def match_station(results, name, code):
    """
    Pick this line's platform from a '<name> MRT STATION' search.

    Fuzzy search ranks exits, bus interchanges and similarly named stations
    (e.g. '<NAME> SOUTH') alongside the real one, so the top hit alone can't
    be trusted. Accept '<NAME> MRT STATION' optionally followed by a code
    suffix such as ' (NS9 / TE2)'. At interchanges OneMap may list each
    line's platform separately, so prefer the hit carrying this row's code.
    """
    wanted   = re.compile(rf"^{re.escape(name.upper())} MRT STATION(?: \((.*)\))?$")
    fallback = None

    for hit in results:
        m = wanted.match(hit.get("SEARCHVAL", ""))
        if not m:
            continue
        codes = (m.group(1) or "").replace(" ", "").split("/")
        if code in codes:
            return hit
        fallback = fallback or hit

    return fallback


if __name__ == "__main__":
    print("=" * 55)
    print("MRT Stations — Seed Ingestion")
    print("=" * 55)

    df = pd.read_csv(SEED)
    print(f"Seed contains {len(df)} station-line rows")

    today   = datetime.now(ZoneInfo("Asia/Singapore")).date()
    opened  = pd.to_datetime(df["opened"], errors="coerce").dt.date
    is_open = opened.notna() & (opened <= today)
    for r in df[~is_open].itertuples():
        print(f"  Skipping {r.station_code} {r.station_name}: not yet open ({r.opened})")
    df = df[is_open].assign(opened=opened[is_open])

    token = get_token()
    print("Geocoding...")

    # One search per station name; interchange lines pick from the same results
    searches, rows, missing = {}, [], []
    for r in df.itertuples():
        if r.station_name not in searches:
            _, searches[r.station_name] = search(f"{r.station_name} MRT STATION", token)
            time.sleep(0.2)

        hit = match_station(searches[r.station_name], r.station_name, r.station_code)
        if hit is None:
            print(f"  NO MATCH: {r.station_code} {r.station_name}")
            missing.append(r.station_code)
            continue

        rows.append({
            "station_name": r.station_name,
            "line":         r.line,
            "station_code": r.station_code,
            "opened":       r.opened,
            "latitude":     float(hit["LATITUDE"]),
            "longitude":    float(hit["LONGITUDE"]),
            "onemap_name":  hit["SEARCHVAL"],
            "_loaded_at":   datetime.now(ZoneInfo("Asia/Singapore")).replace(tzinfo=None),
        })
        print(f"  {r.station_code} {r.station_name} -> {hit['SEARCHVAL']}")

    if missing:
        raise SystemExit(f"{len(missing)} seed station(s) not matched in OneMap: "
                         f"{', '.join(missing)} — {TARGET} left unchanged")

    out = pd.DataFrame(rows)
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE", autodetect=True
    )
    client.load_table_from_dataframe(out, TARGET, job_config=job_config).result()
    print(f"\nLoaded {len(out)} stations to {TARGET}")
