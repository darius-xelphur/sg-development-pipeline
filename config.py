"""Shared configuration and BigQuery client for all pipeline scripts."""

import os
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account

load_dotenv()

ROOT         = Path(__file__).parent
PROJECT_ID   = os.getenv("GCP_PROJECT_ID", "sg-development-analytics")
KEY_PATH     = ROOT / "gcp-key.json"
DATA_GOV_KEY = os.getenv("DATA_GOV_API_KEY")


def get_client():
    """Return an authenticated BigQuery client."""
    credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
    return bigquery.Client(project=PROJECT_ID, credentials=credentials)
