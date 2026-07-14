-- The most recent observed quarter-hour, with derived shares.
CREATE OR REPLACE TABLE mart_latest AS
WITH last AS (
    SELECT *
    FROM raw_eco2mix
    WHERE consommation IS NOT NULL
    ORDER BY date_heure DESC
    LIMIT 1
)
SELECT
    date_heure,
    consommation                                          AS consumption_mw,
    taux_co2                                              AS co2_g_kwh,
    COALESCE(nucleaire, 0)                                AS nuclear_mw,
    COALESCE(eolien, 0)                                   AS wind_mw,
    COALESCE(solaire, 0)                                  AS solar_mw,
    COALESCE(hydraulique, 0)                              AS hydro_mw,
    COALESCE(gaz, 0)                                      AS gas_mw,
    COALESCE(fioul, 0) + COALESCE(charbon, 0)             AS oil_coal_mw,
    COALESCE(bioenergies, 0)                              AS bioenergy_mw,
    COALESCE(pompage, 0)                                  AS pumped_storage_mw,
    COALESCE(ech_physiques, 0)                            AS net_exchange_mw,
    COALESCE(fioul, 0) + COALESCE(charbon, 0) + COALESCE(gaz, 0)
        + COALESCE(nucleaire, 0) + COALESCE(eolien, 0) + COALESCE(solaire, 0)
        + COALESCE(hydraulique, 0) + COALESCE(bioenergies, 0)
                                                          AS production_total_mw,
    ROUND(
        100.0 * (
            COALESCE(eolien, 0) + COALESCE(solaire, 0)
            + COALESCE(hydraulique, 0) + COALESCE(bioenergies, 0)
        ) / NULLIF(
            COALESCE(fioul, 0) + COALESCE(charbon, 0) + COALESCE(gaz, 0)
            + COALESCE(nucleaire, 0) + COALESCE(eolien, 0) + COALESCE(solaire, 0)
            + COALESCE(hydraulique, 0) + COALESCE(bioenergies, 0),
        0),
        1
    )                                                     AS renewable_share_pct
FROM last;
