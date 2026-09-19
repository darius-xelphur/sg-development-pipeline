-- transformations/mart/mart_hdb_woodlands_town_monthly.sql
-- Purpose: Business-ready HDB resale fact table, scoped to Woodlands
-- Source:  staging.hdb_resale_transactions (valid = 1 only)
-- Target:  mart.hdb_woodlands_town_monthly
-- Pattern: full dump — CREATE OR REPLACE drops and rebuilds every run.
--          No delta or incremental logic; no timestamp-based filtering.
-- Note:    storey_range is kept as the published band. An earlier attempt to
--          estimate exact floor from price rank was dropped: 81% of rows fell
--          to the bottom of their band because most comparison groups had too
--          few transactions to rank, making the output an artefact of group
--          size rather than a signal.

CREATE OR REPLACE TABLE `sg-development-analytics.mart.hdb_woodlands_town_monthly` AS

SELECT
  transaction_month                        AS transaction_date,
  town,
  flat_type,
  block,
  street_name,
  CONCAT(block, ' ', street_name)          AS address,
  flat_model,
  storey_range,
  floor_area_sqm,
  lease_commence_date,

  -- "61 years 04 months" -> total months
  (
    CAST(REGEXP_EXTRACT(remaining_lease, r'(\d+)\s+year') AS INT64) * 12
    + IFNULL(CAST(REGEXP_EXTRACT(remaining_lease, r'(\d+)\s+month') AS INT64), 0)
  )                                        AS remaining_lease_months,

  resale_price,
  ROUND(resale_price / NULLIF(floor_area_sqm, 0), 2) AS price_per_sqm,

  CURRENT_DATETIME('Asia/Singapore')       AS _mart_built_at

FROM `sg-development-analytics.staging.hdb_resale_transactions`
WHERE
  valid = 1
  AND town = 'WOODLANDS';
