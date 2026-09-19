from config import get_client, PROJECT_ID

client = get_client()

print(f"Connecting to project: {PROJECT_ID}")
query = "SELECT 'Connection successful' AS status, CURRENT_DATETIME('Asia/Singapore') AS sgt"
for row in client.query(query).result():
    print(f"  {row.status} at {row.sgt}")

print("\nDatasets found:")
for dataset in client.list_datasets():
    print(f"  {dataset.dataset_id}")
