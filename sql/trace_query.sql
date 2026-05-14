-- =============================================================================
-- Trace puller — incremental pull of recent tracking updates for Ryder customers
-- Bind parameters:
--   %(cursor_start)s    : timestamp lower bound (last_synced minus overlap)
--   %(run_started)s     : timestamp upper bound (now)
--   %(customer_codes)s  : tuple of customer codes to include
-- =============================================================================
SELECT
    o.CUSTOMER_ORDER_NUMBER,
    tu.CURRENT_LOCATION_CITY,
    tu.CURRENT_LOCATION_STATE,
    tu.SOURCE_CREATED_AT_UTC,
    tu.SOURCE_CREATED_AT_TIMEZONE,
    tu.CURRENT_LOCATION_LATITUDE,
    tu.CURRENT_LOCATION_LONGITUDE,
    rs.SEQUENCE                                     AS SEQUENCE,
    tu.TRAILER_NUMBER                               AS TRACKING_UPDATES_TRAILER_NUMBER,
    SPLIT_PART(se.TRAILER_NUMBERS, ',', 1)          AS STOP_EVENTS_TRAILER_NUMBERS_FIRST,
    da.TRACTOR_IDENTIFIER                           AS DRIVER_ASSIGNMENTS_TRACTOR_IDENTIFIER,
    se.TRACTOR_NUMBER                               AS STOP_EVENTS_TRACTOR_NUMBER,
    da.DRIVER1_NAME                                 AS DRIVER_ASSIGNMENTS_DRIVER1_NAME
FROM TRACKING_UPDATES tu
JOIN ROUTES r ON tu.ROUTE_ID = r.ROUTE_ID
JOIN ORDERS o ON r.LOAD_NUMBER = o.LOAD_NUMBER
LEFT JOIN ROUTE_STOPS rs ON tu.STOP_ID = rs.ROUTE_STOP_ID
LEFT JOIN DRIVER_ASSIGNMENTS da
       ON tu.ROUTE_ID = da.ROUTE_ID
      AND da.IS_DELETED = FALSE
LEFT JOIN (
    SELECT ROUTE_ID,
           MAX(TRAILER_NUMBERS) AS TRAILER_NUMBERS,
           MAX(TRACTOR_NUMBER)  AS TRACTOR_NUMBER
    FROM STOP_EVENTS
    GROUP BY ROUTE_ID
) se ON tu.ROUTE_ID = se.ROUTE_ID
WHERE o.CUSTOMER_CODE IN (%(customer_codes)s)
  AND tu.UPDATED_AT_UTC >  %(cursor_start)s
  AND tu.UPDATED_AT_UTC <= %(run_started)s
  AND tu.IS_DELETED = FALSE
  AND tu.CURRENT_LOCATION_LATITUDE IS NOT NULL
  AND tu.CURRENT_LOCATION_LONGITUDE IS NOT NULL
  AND o.CUSTOMER_ORDER_NUMBER IS NOT NULL
ORDER BY tu.SOURCE_CREATED_AT_UTC ASC
