-- transformations/mart/mart_woodlands_bus_accessibility.sql
-- Purpose: Bus accessibility metrics per geocoded Woodlands address
-- Source:  raw.address_geocodes (status = 'OK')
--          raw.lta_bus_stops
-- Target:  mart.woodlands_bus_accessibility
-- Grain:   one row per address (NOT per transaction) — addresses are static,
--          so this is computed once and joined to transactions downstream.
-- Pattern: full dump — CREATE OR REPLACE.
-- Note:    distances are straight-line (ST_DISTANCE on WGS84), not walking
--          distance. Real walking distance is longer and depends on the path
--          network; OneMap's routing API could give that, at one call per
--          address-stop pair.

CREATE OR REPLACE TABLE `sg-development-analytics.mart.woodlands_bus_accessibility` AS

WITH addresses AS (
  SELECT
    address,
    block,
    street_name,
    latitude,
    longitude,
    ST_GEOGPOINT(longitude, latitude) AS geo
  FROM `sg-development-analytics.raw.address_geocodes`
  WHERE status = 'OK'
),

stops AS (
  SELECT
    bus_stop_code,
    description,
    road_name,
    ST_GEOGPOINT(longitude, latitude) AS geo
  FROM `sg-development-analytics.raw.lta_bus_stops`
  -- Exclude decommissioned stops parked at (0,0), which would otherwise
  -- register as plausibly-near to everything.
  WHERE latitude != 0 AND longitude != 0
),

-- Every address-stop pair within 1km, with its distance.
pairs AS (
  SELECT
    a.address,
    s.bus_stop_code,
    s.description AS stop_description,
    ST_DISTANCE(a.geo, s.geo) AS metres
  FROM addresses a
  JOIN stops s
    ON ST_DWITHIN(a.geo, s.geo, 1000)
)

SELECT
  a.address,
  a.block,
  a.street_name,
  a.latitude,
  a.longitude,

  -- Nearest stop
  ROUND(MIN(p.metres), 1)                              AS nearest_stop_m,
  ARRAY_AGG(p.stop_description ORDER BY p.metres LIMIT 1)[OFFSET(0)]
                                                       AS nearest_stop_name,

  -- Density of service within walkable bands
  COUNTIF(p.metres <= 200)                             AS stops_within_200m,
  COUNTIF(p.metres <= 400)                             AS stops_within_400m,
  COUNTIF(p.metres <= 800)                             AS stops_within_800m,

  CURRENT_DATETIME('Asia/Singapore')                   AS _mart_built_at

FROM addresses a
LEFT JOIN pairs p ON a.address = p.address
GROUP BY a.address, a.block, a.street_name, a.latitude, a.longitude;
