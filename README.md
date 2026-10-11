# Singapore Development Analytics

A data pipeline that combines Singapore HDB resale transactions with transport
and school accessibility data, to examine whether proximity to amenities is
reflected in property prices.

Covers **all 26 HDB towns island-wide**, around 240,000 resale transactions
since 2017, each tagged with its URA planning region.

**Stack:** Python · BigQuery · GitHub Actions
**Sources:** data.gov.sg · LTA DataMall · OneMap · MOE

---

## What this does

Five public data sources are ingested nightly, cleaned, joined spatially, and
served as analysis-ready tables in BigQuery.

```
data.gov.sg (HDB resale)  ─┐
LTA DataMall (bus stops)  ─┤
OneMap (geocoding)        ─┼──→  raw  ──→  staging  ──→  mart
MOE (schools)             ─┤
MRT seed (version-controlled) ─┘
```

The output is one row per resale transaction, carrying the flat's attributes,
its town and region, plus its block's distance to the nearest bus stop, MRT
station (as of the sale date), primary school and secondary school.

---

## Architecture

### Three layers

| Layer | Purpose | Load pattern |
|---|---|---|
| `raw` | Source data as it arrived. Never transformed. | Full replace, except geocode caches |
| `staging` | Type-cast, keyed, one table per source | Truncate + reload |
| `mart` | Business logic: joins, derived metrics, spatial calculations | Full rebuild |

The separation exists so failures are localisable. If a number looks wrong in
a dashboard you can walk backwards: wrong in `mart` → business logic bug;
right in `mart` but wrong in `staging` → casting problem; wrong in `raw` → the
source changed. Collapse the layers and every bug becomes a hunt through one
long query.

It also limits blast radius. Rebuilding a mart takes seconds. Rebuilding
staging takes seconds. Only `raw` requires going back to the source, and that
is almost never necessary.

### Tables

**raw**
| Table | Rows | Source |
|---|---|---|
| `hdb_resale_transactions` | ~240,000 | data.gov.sg, all towns, Jan 2017 onwards |
| `lta_bus_stops` | ~5,000 | LTA DataMall, island-wide |
| `address_geocodes` | ~9,000 | OneMap, HDB blocks island-wide |
| `mrt_stations` | ~170 | Seed file + OneMap geocoding, one row per line-station code |
| `schools` | 337 | MOE directory, island-wide |
| `school_geocodes` | 337 | OneMap, by postal code |

**staging**
| Table | Notes |
|---|---|
| `hdb_resale_transactions` | Typed, surrogate key, SCD Type 2 columns |

**mart**
| Table | Grain | Notes |
|---|---|---|
| `mrt_stations` | One physical station | Interchanges collapsed |
| `sg_accessibility` | One address | Bus, MRT (current network) and school distances |
| `hdb_resale` | One transaction | The analysis table: all towns, with `region` and point-in-time MRT access |

---

## Data sources

| Source | What | Auth | Update frequency |
|---|---|---|---|
| [data.gov.sg](https://data.gov.sg) | HDB resale transactions | API key | Monthly |
| [LTA DataMall](https://datamall.lta.gov.sg) | Bus stop locations | AccountKey | Ad hoc |
| [OneMap](https://www.onemap.gov.sg/apidocs/) | Address geocoding | Email + password | N/A (lookup service) |
| [data.gov.sg](https://data.gov.sg) | MOE school directory | None | Annual |
| `seeds/mrt_stations.csv` | MRT stations | — | Manual |

All government data is used under the
[Singapore Open Data Licence](https://data.gov.sg/open-data-licence).

### Why MRT stations are a seed file

There is no clean public API for MRT station coordinates. LTA publishes train
station geometry as a shapefile, and OneMap's Themes API appears to require
elevated permissions. A version-controlled CSV of every MRT line-station code,
enriched with OneMap geocoding at load time, is simpler and more transparent
than either alternative.

The seed carries each line's opening date, which matters analytically — see
*Design decisions* below. Planned stations can stay in the seed with
`opened = TBA`; they are skipped at load until given a real date.

---

## Setup

### Prerequisites

- Python 3.12
- A Google Cloud project with BigQuery enabled
- Accounts with data.gov.sg, LTA DataMall, and OneMap

### 1. Clone and create the environment

```bash
git clone https://github.com/<your-username>/sg-development-pipeline.git
cd sg-development-pipeline

conda create -n sg-pipeline python=3.12 -y
conda activate sg-pipeline
pip install -r requirements.txt
```

### 2. Google Cloud

Create a project, enable the **BigQuery API** and **BigQuery Storage API**,
then create three datasets in `asia-southeast1`:

```
raw
staging
mart
```

Create a service account with **BigQuery Data Editor** and **BigQuery Job
User**, download a JSON key, and save it as `gcp-key.json` in the repo root.

`gcp-key.json` is gitignored. It must never be committed.

### 3. Credentials

```bash
cp .env.example .env
```

Fill in `.env`:

```
GCP_PROJECT_ID=your-project-id
DATA_GOV_API_KEY=...
LTA_ACCOUNT_KEY=...
ONEMAP_EMAIL=...
ONEMAP_PASSWORD=...
```

Where to get each:
- **data.gov.sg** — sign in at data.gov.sg, create an API key
- **LTA DataMall** — register at
  [request-for-api](https://datamall.lta.gov.sg/content/datamall/en/request-for-api.html);
  the AccountKey arrives by email
- **OneMap** — register at
  [onemap.gov.sg/apidocs/register](https://www.onemap.gov.sg/apidocs/register);
  your account credentials *are* the API credentials

### 4. Verify

```bash
python test_connection.py
```

Expected: a success message and your three dataset names.

### 5. Run the pipeline

In dependency order:

```bash
python ingestion/hdb/extract_resale.py
python ingestion/lta/extract_bus_stops.py
python ingestion/mrt/extract_stations.py
python ingestion/moe/extract_schools.py

python transformations/run_transformation.py staging/stg_hdb_resale.sql
python ingestion/onemap/geocode_addresses.py

python transformations/run_transformation.py mart/mart_mrt_stations.sql
python transformations/run_transformation.py mart/mart_sg_accessibility.sql
python transformations/run_transformation.py mart/mart_hdb_resale.sql
```

First run takes the better part of an hour; almost all of it is geocoding
~9,000 addresses island-wide. Subsequent runs skip geocoding entirely.

SQL files refer to tables as `` `${project}.dataset.table` ``.
`run_transformation.py` fills in `${project}` from `GCP_PROJECT_ID`, so
ingestion and transformations always target the same project.

---

## Automation

`.github/workflows/pipeline.yml` runs the full pipeline nightly at
**02:00 SGT** (18:00 UTC), and can be triggered manually from the Actions tab.

Required repository secrets:

```
GCP_SA_KEY           full contents of gcp-key.json
GCP_PROJECT_ID       your project id
DATA_GOV_API_KEY
LTA_ACCOUNT_KEY
ONEMAP_EMAIL
ONEMAP_PASSWORD
```

### Step ordering matters

The dependency chain is:

```
ingest (all sources) → staging → geocode → marts
```

Geocoding reads from `staging`, so it must run after it. The accessibility
mart reads the geocode cache, the bus stops and the station mart, so it must
run after all three.

This ordering is implicit in a list of shell commands, which is exactly the
kind of thing an orchestrator exists to make explicit. At this size it is
manageable; past a dozen steps it would not be. See *Known limitations*.

---

## Design decisions

### Geocoding is incremental; everything else is a full dump

Most loads here replace their table entirely on every run — simpler, and
idempotent by construction.

Geocoding is the exception. Each address costs an API call against a free
government service, and a building does not move. So addresses are cached and
only new ones are looked up. A typical nightly run geocodes zero addresses; a
run after a monthly data refresh might geocode one or two.

### SCD Type 2 scaffold in staging

`staging.hdb_resale_transactions` carries `valid_from`, `valid_to`,
`change_time` and `valid` columns. These are not yet exercised, because the
table is truncated and reloaded each run.

They are there so that moving to incremental loading later does not require
redesigning the table. All audit timestamps are Singapore local time
(`DATETIME`, not `TIMESTAMP`) so values read as wall-clock SGT everywhere.

### Accessibility is computed per address, not per transaction

A block's distance to its bus stop is the same for a 2017 sale and a 2024
sale. Computing it once per address and joining to transactions is both
cheaper and conceptually correct.

MRT is the exception, because the network changes over time — see below.

### MRT opening dates are tracked

The Thomson-East Coast Line opened 31 January 2020. Woodlands North and
Woodlands South did not exist before that.

Measuring a 2018 transaction against a station that opened two years later
would attribute a price effect to infrastructure that was not there. So
`mart.hdb_resale` computes MRT access **as of each sale's month**: a station
counts only if it had opened by the first day of that month, and it is an
interchange only once two or more of its lines had opened. `nearest_mrt_m`,
`nearest_mrt_name`, `nearest_mrt_is_interchange` and `mrt_within_800m` are all
point-in-time.

`mart.sg_accessibility` keeps MRT columns for the current network, for maps
and present-day questions. Don't join those to historical sales.

### Interchanges are collapsed at the mart layer, not in raw

Woodlands is served by NS9 (North South Line, 1996) and TE2 (Thomson-East
Coast Line, 2020). `raw.mrt_stations` keeps both rows — they are genuinely
distinct codes with different opening dates. `mart.mrt_stations` collapses
them into one physical station, retaining `line_count` and `is_interchange`
as connectivity measures.

Source grain in raw, business logic in mart.

### Primary and secondary schools are measured differently

The 1km and 2km primary school bands are MOE ballot priority thresholds — a
real mechanism affecting where families want to live, not an arbitrary
distance. Secondary allocation does not work that way, so only distance and a
2km count are kept.

### Region follows URA's five planning regions

`region` maps each HDB town to North, North-East, East, West or Central, using
URA's planning regions. It gives a coarser location control than `town` when
comparing across the island.

### Storey-level price estimation was attempted and dropped

An earlier version tried to infer each flat's exact floor by ranking
transactions on price within its published storey band.

It was dropped. 81% of rows fell to the bottom of their band, because most
comparison groups had too few transactions to rank — the output was an
artefact of group size rather than a signal. `storey_range` is kept as
published.

This is documented because "why is there no storey column?" is a reasonable
question, and the answer is more useful than the column would have been.

---

## Findings

> **To be completed.** The pipeline is built; the analysis write-up is not.
> Replace this section with results from the queries below.

### Does MRT proximity affect price?

```sql
SELECT
  region,
  CASE
    WHEN nearest_mrt_m <= 400  THEN 'a. Under 400m'
    WHEN nearest_mrt_m <= 800  THEN 'b. 400-800m'
    WHEN nearest_mrt_m <= 1200 THEN 'c. 800-1200m'
    ELSE 'd. Over 1200m'
  END                                                     AS mrt_band,
  COUNT(*)                                                AS txns,
  COUNT(DISTINCT address)                                 AS blocks,
  ROUND(APPROX_QUANTILES(price_per_sqm, 2)[OFFSET(1)], 0) AS median_psm
FROM `<project>.mart.hdb_resale`
WHERE nearest_mrt_m IS NOT NULL
  AND flat_type = '4 ROOM'
  AND transaction_date >= '2023-01-01'
GROUP BY region, mrt_band
ORDER BY region, mrt_band;
```

Flat type and date range are held constant, and results are split by region,
so the comparison is not confounded by flat mix, market conditions or broad
location.

### Caveats to state alongside any result

Blocks near MRT stations also tend to be nearer the town centre, differently
aged, and differently sized. A price gap across distance bands is a
**correlation, not a measured premium**. Separating the effect would require
controlling for floor area, remaining lease and storey — a regression, not a
band comparison.

The `blocks` column matters: if a band contains many transactions but few
distinct addresses, the result describes those buildings rather than the
effect of distance.

---

## Known limitations

**Distances are straight-line.** `ST_DISTANCE` on WGS84 gives as-the-crow-flies
metres. A bus stop 150m away across a canal with no bridge is a 600m walk.
OneMap's routing API could give true walking distance at the cost of one call
per address-amenity pair.

**No data quality tests.** A bad upstream refresh would propagate silently.
Row count bounds, null checks and range assertions should fail the pipeline
rather than reach the marts.

**Orchestration is a shell script.** Step dependencies are implicit in the
ordering of commands in a YAML file. dbt or an orchestrator would make the
dependency graph explicit and testable.

**MRT only, no LRT.** The seed covers the six MRT lines but not the Bukit
Panjang, Sengkang or Punggol LRT. In those towns the LRT is the main feeder,
so a block beside an LRT stop can still show a long `nearest_mrt_m`. Rail
access there is understated.

---

## Repository structure

```
sg-development-pipeline/
├── .github/workflows/
│   └── pipeline.yml                  Nightly orchestration
├── ingestion/
│   ├── hdb/extract_resale.py         data.gov.sg resale transactions
│   ├── lta/extract_bus_stops.py      LTA DataMall bus stops
│   ├── mrt/extract_stations.py       MRT seed + geocoding
│   ├── moe/extract_schools.py        MOE schools + geocoding
│   └── onemap/
│       ├── client.py                 Shared OneMap auth + search
│       └── geocode_addresses.py      HDB address geocoding (cached)
├── transformations/
│   ├── run_transformation.py         Generic SQL runner
│   ├── staging/
│   │   └── stg_hdb_resale.sql
│   └── mart/
│       ├── mart_mrt_stations.sql
│       ├── mart_sg_accessibility.sql
│       └── mart_hdb_resale.sql
├── seeds/
│   └── mrt_stations.csv        Version-controlled reference data
├── config.py                         Shared credentials and BigQuery client
├── test_connection.py
├── requirements.txt
├── LICENSE
└── .env.example
```

---

## Next steps

- Dashboard (Looker Studio connects live to BigQuery at no cost)
- Before/after analysis of the TEL opening in Jan 2020 — the data supports
  this and it is the most interesting question available
- Data quality tests
- Migrate transformations to dbt
- Add LRT stations to the seed, flagged by line type

---

## Licence

Code: MIT — see [LICENSE](LICENSE).
Data: [Singapore Open Data Licence](https://data.gov.sg/open-data-licence).
