"""
Ingest HDB resale flat transactions from data.gov.sg into BigQuery raw layer.

Flow: initiate-download -> poll-download -> fetch CSV -> validate -> load
Load pattern: full replace (WRITE_TRUNCATE) — idempotent, safe to re-run.
"""

import io
import sys
import time
import requests
from google.cloud import bigquery
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from config import get_client, PROJECT_ID, DATA_GOV_KEY

DATASET_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
BASE_URL   = "https://api-open.data.gov.sg/v1/public/api/datasets"
TARGET     = f"{PROJECT_ID}.raw.hdb_resale_transactions"
HEADERS    = {"x-api-key": DATA_GOV_KEY}

client = get_client()


def initiate_download():
    print("Step 1: Initiating download...")
    r = requests.get(f"{BASE_URL}/{DATASET_ID}/initiate-download",
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise ValueError(f"Initiate failed: {data}")
    print("  Initiated")


def poll_download(max_attempts=20, wait=6):
    print("Step 2: Polling for readiness...")
    for attempt in range(1, max_attempts + 1):
        r = requests.get(f"{BASE_URL}/{DATASET_ID}/poll-download",
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        status = data["data"].get("status")
        print(f"  Attempt {attempt}: {status}")

        if status == "DOWNLOAD_SUCCESS":
            return data["data"]["url"]
        if status == "ERROR":
            raise ValueError(f"Poll failed: {data}")
        time.sleep(wait)

    raise TimeoutError(f"Not ready after {max_attempts} attempts")


def download_csv(url):
    print("Step 3: Downloading CSV...")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    print(f"  {len(df):,} rows, {len(df.columns)} columns")
    return df


def validate(df):
    print("Step 4: Validating...")
    if df.empty:
        raise ValueError("Empty dataframe — aborting")

    required = ["month", "town", "flat_type", "resale_price", "floor_area_sqm"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    for col in ["resale_price", "floor_area_sqm", "month"]:
        nulls = df[col].isna().sum()
        if nulls:
            print(f"  Warning: {nulls:,} nulls in {col}")

    print(f"  Passed — {len(df):,} rows")
    return df


def load(df):
    print(f"Step 5: Loading to {TARGET}...")
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        autodetect=True,
    )
    client.load_table_from_dataframe(df, TARGET, job_config=job_config).result()
    print(f"  Loaded {client.get_table(TARGET).num_rows:,} rows")


if __name__ == "__main__":

    print("=" * 55)
    print("HDB Resale Transactions — Raw Ingestion")
    print("=" * 55)

    initiate_download()
    url = poll_download()
    df  = download_csv(url)
    df  = validate(df)
    load(df)

    print("\nPipeline complete.")
