-- transformations/mart/mart_woodlands_accessibility.sql
-- Purpose: Accessibility per geocoded Woodlands address — bus, MRT, schools
-- Source:  raw.address_geocodes (status = 'OK')
--          raw.lta_bus_stops
--          mart.mrt_stations
--          raw.schools + raw.school_geocodes
-- Target:  mart.woodlands_bus_accessibility
-- Grain:   one row per address; addresses are static, so this is computed
--          once and joined to transactions downstream.
-- Pattern: full dump — CREATE OR REPLACE.
--
-- Distances are straight-line (ST_DISTANCE on WGS84), not walking distance.
-- Real walking distance is longer and depends on the path network.
--
-- Two MRT distances are computed:
--   nearest_mrt_m       — to any station, ignoring when it opened
--   nearest_mrt_2019_m  — to stations open before the TEL launch (Jan 2020)
-- Transactions before 2020 should use the second, or they credit a station
-- that did not yet exist.
--
-- Primary and secondary schools are kept separate: the 1km and 2km primary
-- bands are MOE ballot priority thresholds, a real mechanism affecting where
-- families want to live. Secondary allocation does not work that way.

CREATE OR REPLACE TABLE `${project}.mart.woodlands_bus_accessibility` AS

WITH addresses AS (
  SELECT
    address, block, street_name, latitude, longitude,
    ST_GEOGPOINT(longitude, latitude) AS geo
  FROM `${project}.raw.address_geocodes`
  WHERE status = 'OK'
),

stops AS (
  SELECT
    description AS stop_description,
    ST_GEOGPOINT(longitude, latitude) AS geo
  FROM `${project}.raw.lta_bus_stops`
  WHERE latitude != 0 AND longitude != 0
),

stations AS (
  SELECT
    station_name, station_codes, is_interchange, first_opened,
    ST_GEOGPOINT(longitude, latitude) AS geo
  FROM `${project}.mart.mrt_stations`
),

schools AS (
  SELECT
    s.school_name,
    s.mainlevel_code,
    ST_GEOGPOINT(g.longitude, g.latitude) AS geo
  FROM `${project}.raw.schools` s
  JOIN `${project}.raw.school_geocodes` g
    ON s.postal_code = g.postal_code
  WHERE g.status = 'OK'
),

bus_pairs AS (
  SELECT a.address, s.stop_description, ST_DISTANCE(a.geo, s.geo) AS metres
  FROM addresses a
  JOIN stops s ON ST_DWITHIN(a.geo, s.geo, 1000)
),

-- Wider radius: people walk further to a train than to a bus.
mrt_pairs AS (
  SELECT
    a.address,
    st.station_name,
    st.is_interchange,
    st.first_opened,
    ST_DISTANCE(a.geo, st.geo) AS metres
  FROM addresses a
  JOIN stations st ON ST_DWITHIN(a.geo, st.geo, 3000)
),

school_pairs AS (
  SELECT
    a.address,
    sc.school_name,
    sc.mainlevel_code,
    ST_DISTANCE(a.geo, sc.geo) AS metres
  FROM addresses a
  JOIN schools sc ON ST_DWITHIN(a.geo, sc.geo, 3000)
),

bus_agg AS (
  SELECT
    address,
    ROUND(MIN(metres), 1) AS nearest_stop_m,
    ARRAY_AGG(stop_description ORDER BY metres LIMIT 1)[OFFSET(0)] AS nearest_stop_name,
    COUNTIF(metres <= 200) AS stops_within_200m,
    COUNTIF(metres <= 400) AS stops_within_400m,
    COUNTIF(metres <= 800) AS stops_within_800m
  FROM bus_pairs
  GROUP BY address
),

mrt_agg AS (
  SELECT
    address,
    ROUND(MIN(metres), 1) AS nearest_mrt_m,
    ARRAY_AGG(station_name ORDER BY metres LIMIT 1)[OFFSET(0)] AS nearest_mrt_name,
    ARRAY_AGG(is_interchange ORDER BY metres LIMIT 1)[OFFSET(0)] AS nearest_mrt_is_interchange,
    COUNTIF(metres <= 800)  AS mrt_within_800m,
    COUNTIF(metres <= 1500) AS mrt_within_1500m,

    -- Pre-TEL view: only stations open before 2020-01-31
    ROUND(MIN(IF(first_opened < '2020-01-31', metres, NULL)), 1) AS nearest_mrt_2019_m
  FROM mrt_pairs
  GROUP BY address
),

school_agg AS (
  SELECT
    address,

    ROUND(MIN(IF(mainlevel_code = 'PRIMARY', metres, NULL)), 1) AS nearest_primary_m,
    ARRAY_AGG(IF(mainlevel_code = 'PRIMARY', school_name, NULL) IGNORE NULLS
              ORDER BY metres LIMIT 1)[SAFE_OFFSET(0)]          AS nearest_primary_name,
    COUNTIF(mainlevel_code = 'PRIMARY' AND metres <= 1000)      AS primary_within_1km,
    COUNTIF(mainlevel_code = 'PRIMARY' AND metres <= 2000)      AS primary_within_2km,

    ROUND(MIN(IF(mainlevel_code LIKE 'SECONDARY%', metres, NULL)), 1) AS nearest_secondary_m,
    COUNTIF(mainlevel_code LIKE 'SECONDARY%' AND metres <= 2000)      AS secondary_within_2km
  FROM school_pairs
  GROUP BY address
)

SELECT
  a.address, a.block, a.street_name, a.latitude, a.longitude,

  b.nearest_stop_m,
  b.nearest_stop_name,
  b.stops_within_200m,
  b.stops_within_400m,
  b.stops_within_800m,

  m.nearest_mrt_m,
  m.nearest_mrt_name,
  m.nearest_mrt_is_interchange,
  m.mrt_within_800m,
  m.mrt_within_1500m,
  m.nearest_mrt_2019_m,

  sc.nearest_primary_m,
  sc.nearest_primary_name,
  sc.primary_within_1km,
  sc.primary_within_2km,
  sc.nearest_secondary_m,
  sc.secondary_within_2km,

  CURRENT_DATETIME('Asia/Singapore') AS _mart_built_at

FROM addresses a
LEFT JOIN bus_agg    b  ON a.address = b.address
LEFT JOIN mrt_agg    m  ON a.address = m.address
LEFT JOIN school_agg sc ON a.address = sc.address;