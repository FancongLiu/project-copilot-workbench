WITH bounds AS (
    SELECT min(timestamp) AS start_time, max(timestamp) AS end_time
    FROM telemetry_clean
)
SELECT
    'coverage' AS event_type,
    asset_id,
    CAST(min(timestamp) AS VARCHAR) AS start_time,
    CAST(max(timestamp) AS VARCHAR) AS end_time,
    count(*) AS sample_count,
    CAST(date_diff('second', bounds.start_time, bounds.end_time) / 10 + 1 AS BIGINT)
        AS expected_samples,
    CAST(date_diff('second', bounds.start_time, bounds.end_time) / 10 + 1 - count(*) AS BIGINT)
        AS missing_samples,
    round(count(*) * 100.0 / (date_diff('second', bounds.start_time, bounds.end_time) / 10 + 1), 6)
        AS completeness_pct,
    'telemetry.csv' AS source_file
FROM telemetry_clean, bounds
GROUP BY asset_id, bounds.start_time, bounds.end_time
ORDER BY asset_id;

WITH duplicate_keys AS (
    SELECT asset_id, timestamp, count(*) AS row_count
    FROM telemetry_raw
    GROUP BY asset_id, timestamp
    HAVING count(*) > 1
)
SELECT
    'duplicate_timestamp' AS event_type,
    asset_id,
    CAST(min(timestamp) AS VARCHAR) AS start_time,
    CAST(max(timestamp) AS VARCHAR) AS end_time,
    count(*) AS event_count,
    sum(row_count - 1) AS duplicate_rows,
    'telemetry.csv' AS source_file
FROM duplicate_keys
GROUP BY asset_id
ORDER BY asset_id;

WITH ordered AS (
    SELECT
        asset_id,
        timestamp,
        lag(timestamp) OVER (PARTITION BY asset_id ORDER BY ingest_seq) AS previous_timestamp
    FROM telemetry_raw
)
SELECT
    'out_of_order' AS event_type,
    asset_id,
    CAST(min(timestamp) AS VARCHAR) AS start_time,
    CAST(max(timestamp) AS VARCHAR) AS end_time,
    count(*) AS event_count,
    'telemetry.csv' AS source_file
FROM ordered
WHERE timestamp < previous_timestamp
GROUP BY asset_id
ORDER BY asset_id;

WITH signatures AS (
    SELECT
        asset_id,
        timestamp,
        hash(
            ambient_temp_c,
            return_air_temp_c,
            supply_air_temp_c,
            suction_temp_c,
            discharge_temp_c,
            liquid_temp_c,
            suction_pressure_kpa_g,
            discharge_pressure_kpa_g,
            compressor_fb_hz
        ) AS signature
    FROM telemetry_clean
), boundaries AS (
    SELECT
        *,
        CASE
            WHEN signature = lag(signature) OVER (PARTITION BY asset_id ORDER BY timestamp)
             AND date_diff('second', lag(timestamp) OVER (PARTITION BY asset_id ORDER BY timestamp), timestamp) = 10
            THEN 0 ELSE 1
        END AS new_group
    FROM signatures
), grouped AS (
    SELECT
        *,
        sum(new_group) OVER (PARTITION BY asset_id ORDER BY timestamp) AS group_id
    FROM boundaries
)
SELECT
    'frozen_sensor_tuple' AS event_type,
    asset_id,
    CAST(min(timestamp) AS VARCHAR) AS start_time,
    CAST(max(timestamp) + INTERVAL 10 SECOND AS VARCHAR) AS end_time,
    count(*) AS sample_count,
    count(*) * 10 AS duration_seconds,
    'telemetry.csv' AS source_file
FROM grouped
GROUP BY asset_id, group_id
HAVING count(*) >= 6
ORDER BY sample_count DESC, asset_id;
