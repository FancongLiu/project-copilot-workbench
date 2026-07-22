SELECT
    asset_id,
    round(sum(thermal_output_kw), 6) AS total_thermal_kw_samples,
    round(sum(electric_power_kw), 6) AS total_electric_kw_samples,
    round(sum(thermal_output_kw) / nullif(sum(electric_power_kw), 0), 6) AS load_weighted_cop,
    CAST(min(timestamp) AS VARCHAR) AS start_time,
    CAST(max(timestamp) AS VARCHAR) AS end_time,
    count(*) AS sample_count,
    'telemetry.csv' AS source_file
FROM telemetry_clean
GROUP BY asset_id
ORDER BY load_weighted_cop DESC;
