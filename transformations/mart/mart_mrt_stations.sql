-- transformations/mart/mart_mrt_stations.sql
-- Purpose: One row per PHYSICAL station, collapsing interchanges
-- Source:  raw.mrt_stations (one row per line-station code)
-- Target:  mart.mrt_stations
--
-- Woodlands NS9 and TE2 are the same physical station served by two lines.
-- For distance purposes that is one point; for connectivity it is better
-- than a single-line station, so line_count is kept as a quality measure.
--
-- opened = earliest line, i.e. when the station first became accessible.
-- The later TEL date is retained separately since 2020 changed the station's
-- connectivity without changing its location.

CREATE OR REPLACE TABLE `sg-development-analytics.mart.mrt_stations` AS

SELECT
  station_name,
  ROUND(AVG(latitude), 6)                       AS latitude,
  ROUND(AVG(longitude), 6)                      AS longitude,

  STRING_AGG(station_code, ' / ' ORDER BY station_code) AS station_codes,
  STRING_AGG(line, ' / ' ORDER BY line)                 AS lines_served,
  COUNT(*)                                      AS line_count,
  COUNT(*) > 1                                  AS is_interchange,

  MIN(opened)                                   AS first_opened,
  MAX(opened)                                   AS latest_line_opened,

  CURRENT_DATETIME('Asia/Singapore')            AS _mart_built_at

FROM `sg-development-analytics.raw.mrt_stations`
GROUP BY station_name;
