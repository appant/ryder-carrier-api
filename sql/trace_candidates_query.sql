-- =============================================================================
-- Trace diagnostic — counts rows that COULD have been pulled (before the
-- Ship ID inner-join in trace_query.sql drops Ship-ID-less orders).
--
-- Returns ONE row:
--   ROWS_BEFORE_SHIP_ID_FILTER : total tracking updates matching window + customer
--   ROWS_WITH_SHIP_ID          : subset that also has a Ship ID
--   ROWS_WITHOUT_SHIP_ID       : the silent-skip bucket (the gap we want visible)
--
-- Same bind params as trace_query.sql.
-- =============================================================================
SELECT
    COUNT(*)                AS ROWS_BEFORE_SHIP_ID_FILTER,
    COUNT(sid.SHIP_ID)      AS ROWS_WITH_SHIP_ID,
    COUNT(*) - COUNT(sid.SHIP_ID) AS ROWS_WITHOUT_SHIP_ID
FROM TRACKING_UPDATES tu
JOIN ROUTES r ON tu.ROUTE_ID = r.ROUTE_ID
JOIN ORDERS o ON r.LOAD_NUMBER = o.LOAD_NUMBER
LEFT JOIN (
    SELECT ORDER_ID,
           MAX_BY(VALUE, UPDATED_AT_UTC) AS SHIP_ID
    FROM ORDER_REFERENCES
    WHERE REFERENCE_TYPE = 'Ship ID'
      AND IS_DELETED = FALSE
      AND VALUE IS NOT NULL
      AND TRIM(VALUE) <> ''
    GROUP BY ORDER_ID
) sid ON sid.ORDER_ID = o.ORDER_ID
WHERE o.CUSTOMER_CODE IN (%(customer_codes)s)
  AND tu.UPDATED_AT_UTC >  %(cursor_start)s
  AND tu.UPDATED_AT_UTC <= %(run_started)s
  AND tu.IS_DELETED = FALSE
  AND tu.CURRENT_LOCATION_LATITUDE IS NOT NULL
  AND tu.CURRENT_LOCATION_LONGITUDE IS NOT NULL
