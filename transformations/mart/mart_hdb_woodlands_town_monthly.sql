-- transformations/mart/mart_hdb_woodlands_town_monthly.sql
-- Purpose: Woodlands HDB resale fact table, enriched with OneMap coordinates
--          and LTA bus accessibility
-- Source:  staging.hdb_resale_transactions (valid = 1)
--          raw.address_geocodes (status = 'OK')
--          mart.woodlands_bus_accessibility
-- Target:  mart.hdb_woodlands_town_monthly
-- Pattern: full dump — CREATE OR REPLACE drops and rebuilds every run.
-- Joins:   LEFT, so a transaction is never dropped for missing enrichment.
-- Note:    storey_range kept as the published band — an earlier attempt to
--          estimate exact floor from price rank was dropped as 81% of rows
--          defaulted to the bottom of their band.

CREATE OR REPLACE TABLE `${project}.mart.hdb_woodlands_town_monthly` AS

SELECT
  s.transaction_month                        AS transaction_date,
  s.town,
  s.flat_type,
  s.block,
  s.street_name,
  CONCAT(s.block, ' ', s.street_name)        AS address,
  s.flat_model,
  s.storey_range,
  s.floor_area_sqm,
  s.lease_commence_date,

  (
    CAST(REGEXP_EXTRACT(s.remaining_lease, r'(\d+)\s+year') AS INT64) * 12
    + IFNULL(CAST(REGEXP_EXTRACT(s.remaining_lease, r'(\d+)\s+month') AS INT64), 0)
  )                                          AS remaining_lease_months,

  s.resale_price,
  ROUND(s.resale_price / NULLIF(s.floor_area_sqm, 0), 2) AS price_per_sqm,

  -- Location (OneMap)
  g.latitude,
  g.longitude,
  g.postal_code,

  -- Bus accessibility (LTA)
  b.nearest_stop_m,
  b.nearest_stop_name,
  b.stops_within_200m,
  b.stops_within_400m,
  b.stops_within_800m,
  b.nearest_mrt_m,
  b.nearest_mrt_name,
  b.nearest_mrt_is_interchange,
  b.mrt_within_800m,
  b.nearest_mrt_2019_m,

  -- School accessibility (MOE)
  b.nearest_primary_m,
  b.nearest_primary_name,
  b.primary_within_1km,
  b.primary_within_2km,
  b.nearest_secondary_m,
  b.secondary_within_2km,

  CURRENT_DATETIME('Asia/Singapore')         AS _mart_built_at

FROM `${project}.staging.hdb_resale_transactions` s
LEFT JOIN `${project}.raw.address_geocodes` g
  ON CONCAT(s.block, ' ', s.street_name) = g.address
 AND g.status = 'OK'
LEFT JOIN `${project}.mart.woodlands_bus_accessibility` b
  ON CONCAT(s.block, ' ', s.street_name) = b.address
WHERE
  s.valid = 1
  AND s.town = 'WOODLANDS';
