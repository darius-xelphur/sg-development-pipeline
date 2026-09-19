"""
Run a SQL transformation file against BigQuery.

Usage:
    python transformations/run_transformation.py staging/stg_hdb_resale.sql
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import get_client

SQL_DIR = Path(__file__).parent
client  = get_client()


def run(relative_path):
    path = SQL_DIR / relative_path
    if not path.exists():
        raise FileNotFoundError(f"Not found: {path}")

    print(f"Running {relative_path}")
    job = client.query(path.read_text())
    job.result()
    print(f"  Done — job {job.job_id}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python transformations/run_transformation.py <path/to/file.sql>\n")
        print("Available:")
        for f in sorted(SQL_DIR.rglob("*.sql")):
            print(f"  {f.relative_to(SQL_DIR)}")
        sys.exit(1)

    run(sys.argv[1])
