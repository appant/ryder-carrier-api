-- =============================================================================
-- Milestone diagnostic — counts rows that COULD have been pulled (before the
-- Ship ID inner-join in milestone_query.sql drops Ship-ID-less orders).
--
-- Returns ONE row:
--   ROWS_BEFORE_SHIP_ID_FILTER : total events matching window + customer filter
--   ROWS_WITH_SHIP_ID          : subset that also has a Ship ID
--   ROWS_WITHOUT_SHIP_ID       : the silent-skip bucket (the gap we want visible)
--
-- Same bind params as milestone_query.sql.
-- =============================================================================
SELECT
    COUNT(*)                AS ROWS_BEFORE_SHIP_ID_FILTER,
    COUNT(sid.SHIP_ID)      AS ROWS_WITH_SHIP_ID,
    COUNT(*) - COUNT(sid.SHIP_ID) AS ROWS_WITHOUT_SHIP_ID
FROM STOP_EVENTS se
JOIN ROUTE_STOPS rs ON se.ROUTE_STOP_ID = rs.ROUTE_STOP_ID
JOIN ROUTES r       ON rs.ROUTE_ID = r.ROUTE_ID
JOIN ORDERS o       ON r.LOAD_NUMBER = o.LOAD_NUMBER
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
  AND se.UPDATED_AT_UTC >  %(cursor_start)s
  AND se.UPDATED_AT_UTC <= %(run_started)s
  AND se.ACTUAL_EVENT_AT_UTC IS NOT NULL
