-- Production mix over the last 7 observed days, long format.
CREATE OR REPLACE TABLE mart_mix_7d AS
WITH observed AS (
    SELECT *
    FROM raw_eco2mix
    WHERE consommation IS NOT NULL
      AND date_heure >= (
          SELECT max(date_heure) - INTERVAL 7 DAY
          FROM raw_eco2mix
          WHERE consommation IS NOT NULL
      )
),
long AS (
    SELECT 'nuclear' AS source, COALESCE(nucleaire, 0) AS mw FROM observed
    UNION ALL SELECT 'wind', COALESCE(eolien, 0) FROM observed
    UNION ALL SELECT 'solar', COALESCE(solaire, 0) FROM observed
    UNION ALL SELECT 'hydro', COALESCE(hydraulique, 0) FROM observed
    UNION ALL SELECT 'gas', COALESCE(gaz, 0) FROM observed
    UNION ALL SELECT 'oil', COALESCE(fioul, 0) FROM observed
    UNION ALL SELECT 'coal', COALESCE(charbon, 0) FROM observed
    UNION ALL SELECT 'bioenergy', COALESCE(bioenergies, 0) FROM observed
)
SELECT
    source,
    ROUND(SUM(mw) / 4.0)                                        AS mwh,
    ROUND(100.0 * SUM(mw) / NULLIF(SUM(SUM(mw)) OVER (), 0), 1) AS share_pct
FROM long
GROUP BY source
ORDER BY mwh DESC;
