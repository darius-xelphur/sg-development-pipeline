-- transformations/mart/mart_hdb_woodlands_town_monthly.sql
-- Purpose: Island-wide HDB resale fact table, enriched with OneMap coordinates,
--          bus, MRT and school accessibility
-- Source:  staging.hdb_resale_transactions (valid = 1)
--          mart.woodlands_bus_accessibility (location, bus, schools)
--          mart.mrt_stations + raw.mrt_stations (MRT as of sale date)
-- Target:  mart.hdb_woodlands_town_monthly
-- Pattern: full dump — CREATE OR REPLACE drops and rebuilds every run.
-- Joins:   LEFT, so a transaction is never dropped for missing enrichment.
-- Note:    storey_range kept as the published band — an earlier attempt to
--          estimate exact floor from price rank was dropped as 81% of rows
--          defaulted to the bottom of their band.
--
-- MRT access is point-in-time. A station counts only if it had opened by the
-- first day of the sale's month, and is an interchange only if two or more of
-- its lines had. So a 2018 sale near Woodlands North sees no TEL station, and
-- Woodlands is not an interchange for it. Bus stops and schools are taken as
-- static from the accessibility mart.
--
-- region follows URA's five planning regions. The ELSE branch is Central:
-- Bishan, Bukit Merah, Bukit Timah, Central Area, Geylang, Kallang/Whampoa,
-- Marine Parade, Queenstown, Toa Payoh.

CREATE OR REPLACE TABLE `${project}.mart.hdb_woodlands_town_monthly` AS

WITH txns AS (
  SELECT
    s.*,
    CONCAT(s.block, ' ', s.street_name) AS address
  FROM `${project}.staging.hdb_resale_transactions` s
  WHERE s.valid = 1
),

address_months AS (
  SELECT DISTINCT
    t.address,
    t.transaction_month,
    ST_GEOGPOINT(b.longitude, b.latitude) AS geo
  FROM txns t
  JOIN `${project}.mart.woodlands_bus_accessibility` b
    ON t.address = b.address
),

stations AS (
  SELECT station_name, ST_GEOGPOINT(longitude, latitude) AS geo
  FROM `${project}.mart.mrt_stations`
),

-- One row per (address, month, station) open at that month; lines_open counts
-- the station's lines that had opened, for point-in-time interchange status.
mrt_pairs AS (
  SELECT
    am.address,
    am.transaction_month,
    st.station_name,
    ANY_VALUE(ST_DISTANCE(am.geo, st.geo)) AS metres,
    COUNT(*)                               AS lines_open
  FROM address_months am
  JOIN stations st
    ON ST_DWITHIN(am.geo, st.geo, 3000)
  JOIN `${project}.raw.mrt_stations` l
    ON l.station_name = st.station_name
   AND l.opened <= am.transaction_month
  GROUP BY am.address, am.transaction_month, st.station_name
),

mrt_asof AS (
  SELECT
    address,
    transaction_month,
    ROUND(MIN(metres), 1)                                         AS nearest_mrt_m,
    ARRAY_AGG(station_name ORDER BY metres LIMIT 1)[OFFSET(0)]    AS nearest_mrt_name,
    ARRAY_AGG(lines_open > 1 ORDER BY metres LIMIT 1)[OFFSET(0)]  AS nearest_mrt_is_interchange,
    COUNTIF(metres <= 800)                                        AS mrt_within_800m
  FROM mrt_pairs
  GROUP BY address, transaction_month
)

SELECT
  s.transaction_month                        AS transaction_date,
  s.town,
  CASE
    WHEN s.town IN ('SEMBAWANG', 'WOODLANDS', 'YISHUN')
      THEN 'North'
    WHEN s.town IN ('ANG MO KIO', 'HOUGANG', 'PUNGGOL', 'SENGKANG', 'SERANGOON')
      THEN 'North-East'
    WHEN s.town IN ('BEDOK', 'PASIR RIS', 'TAMPINES')
      THEN 'East'
    WHEN s.town IN ('BUKIT BATOK', 'BUKIT PANJANG', 'CHOA CHU KANG',
                    'CLEMENTI', 'JURONG EAST', 'JURONG WEST')
      THEN 'West'
    ELSE 'Central'
  END                                        AS region,
  s.flat_type,
  s.block,
  s.street_name,
  s.address,
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
  b.latitude,
  b.longitude,
  b.postal_code,

  -- Bus accessibility (LTA)
  b.nearest_stop_m,
  b.nearest_stop_name,
  b.stops_within_200m,
  b.stops_within_400m,
  b.stops_within_800m,

  -- MRT accessibility, as of the sale month
  m.nearest_mrt_m,
  m.nearest_mrt_name,
  m.nearest_mrt_is_interchange,
  m.mrt_within_800m,

  -- School accessibility (MOE)
  b.nearest_primary_m,
  b.nearest_primary_name,
  b.primary_within_1km,
  b.primary_within_2km,
  b.nearest_secondary_m,
  b.secondary_within_2km,

  CURRENT_DATETIME('Asia/Singapore')         AS _mart_built_at

FROM txns s
LEFT JOIN `${project}.mart.woodlands_bus_accessibility` b
  ON s.address = b.address
LEFT JOIN mrt_asof m
  ON s.address = m.address
 AND s.transaction_month = m.transaction_month;
