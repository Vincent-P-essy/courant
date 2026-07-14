-- Daily aggregates over observed quarter-hours, in French local time.
-- Energy: a quarter-hour at X MW is X/4 MWh.
CREATE OR REPLACE TABLE mart_daily AS
WITH observed AS (
    SELECT *, timezone('Europe/Paris', date_heure) AS local_dt
    FROM raw_eco2mix
    WHERE consommation IS NOT NULL
)
SELECT
    CAST(local_dt AS DATE)                                   AS day,
    COUNT(*)                                                 AS quarters_observed,
    ROUND(AVG(consommation))                                 AS avg_consumption_mw,
    MAX(consommation)                                        AS peak_consumption_mw,
    ROUND(SUM(consommation) / 4.0)                           AS energy_consumed_mwh,
    ROUND(SUM(COALESCE(nucleaire, 0)) / 4.0)                 AS nuclear_mwh,
    ROUND(SUM(COALESCE(eolien, 0)) / 4.0)                    AS wind_mwh,
    ROUND(SUM(COALESCE(solaire, 0)) / 4.0)                   AS solar_mwh,
    ROUND(SUM(COALESCE(hydraulique, 0)) / 4.0)               AS hydro_mwh,
    ROUND(SUM(COALESCE(gaz, 0)) / 4.0)                       AS gas_mwh,
    ROUND(SUM(COALESCE(fioul, 0) + COALESCE(charbon, 0)) / 4.0) AS oil_coal_mwh,
    ROUND(SUM(COALESCE(bioenergies, 0)) / 4.0)               AS bioenergy_mwh,
    ROUND(
        100.0 * SUM(
            COALESCE(eolien, 0) + COALESCE(solaire, 0)
            + COALESCE(hydraulique, 0) + COALESCE(bioenergies, 0)
        ) / NULLIF(SUM(
            COALESCE(fioul, 0) + COALESCE(charbon, 0) + COALESCE(gaz, 0)
            + COALESCE(nucleaire, 0) + COALESCE(eolien, 0) + COALESCE(solaire, 0)
            + COALESCE(hydraulique, 0) + COALESCE(bioenergies, 0)
        ), 0),
        1
    )                                                        AS renewable_share_pct,
    ROUND(AVG(taux_co2), 1)                                  AS avg_co2_g_kwh
FROM observed
GROUP BY 1
ORDER BY 1;
