-- transformations/staging/stg_hdb_resale.sql
-- Purpose: Type-cast raw HDB resale data into the staging layer
-- Source:  raw.hdb_resale_transactions
-- Target:  staging.hdb_resale_transactions
-- Pattern: full truncate + reload on every run
-- SCD:     Type 2 columns present for future incremental loading
-- Time:    all audit timestamps in Singapore local time

CREATE TABLE IF NOT EXISTS `sg-development-analytics.staging.hdb_resale_transactions` (
  surrogate_id         INT64     NOT NULL,
  transaction_month    DATE,
  town                 STRING,
  flat_type            STRING,
  block                STRING,
  street_name          STRING,
  storey_range         STRING,
  floor_area_sqm       FLOAT64,
  flat_model           STRING,
  lease_commence_date  STRING,
  remaining_lease      STRING,
  resale_price         FLOAT64,
  valid_from           DATETIME  NOT NULL,
  valid_to             DATETIME  NOT NULL,
  change_time          DATETIME  NOT NULL,
  valid                INT64     NOT NULL
);

TRUNCATE TABLE `sg-development-analytics.staging.hdb_resale_transactions`;

INSERT INTO `sg-development-analytics.staging.hdb_resale_transactions`
SELECT
  ROW_NUMBER() OVER (
    ORDER BY month, town, flat_type, block, street_name
  )                                        AS surrogate_id,

  PARSE_DATE('%Y-%m', month)              AS transaction_month,
  town,
  flat_type,
  block,
  street_name,
  storey_range,
  CAST(floor_area_sqm AS FLOAT64)          AS floor_area_sqm,
  flat_model,
  CAST(lease_commence_date AS STRING)      AS lease_commence_date,
  remaining_lease,
  CAST(resale_price AS FLOAT64)            AS resale_price,

  CURRENT_DATETIME('Asia/Singapore')       AS valid_from,
  DATETIME('9999-12-31 00:00:00')          AS valid_to,
  CURRENT_DATETIME('Asia/Singapore')       AS change_time,
  1                                        AS valid

FROM `sg-development-analytics.raw.hdb_resale_transactions`
WHERE
  resale_price   IS NOT NULL
  AND floor_area_sqm IS NOT NULL
  AND month      IS NOT NULL;
