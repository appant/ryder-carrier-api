-- =============================================================================
-- Milestone puller — incremental pull of recent stop events for Ryder customers
-- Bind parameters:
--   %(cursor_start)s    : timestamp lower bound (last_synced minus overlap)
--   %(run_started)s     : timestamp upper bound (now)
--   %(customer_codes)s  : tuple of customer codes to include
-- =============================================================================
SELECT
    o.CUSTOMER_ORDER_NUMBER,
    se.EVENT_TYPE,
    se.LATE_ARRIVAL_REASON_CODE,
    se.ACTUAL_EVENT_AT_UTC,
    se.ACTUAL_TIMEZONE,
    rs.LOCALITY,
    rs.ADMINISTRATIVE_AREA1_CODE,
    rs.SEQUENCE
FROM STOP_EVENTS se
JOIN ROUTE_STOPS rs ON se.ROUTE_STOP_ID = rs.ROUTE_STOP_ID
JOIN ROUTES r       ON rs.ROUTE_ID = r.ROUTE_ID
JOIN ORDERS o       ON r.LOAD_NUMBER = o.LOAD_NUMBER
WHERE o.CUSTOMER_CODE IN (%(customer_codes)s)
  AND se.UPDATED_AT_UTC >  %(cursor_start)s
  AND se.UPDATED_AT_UTC <= %(run_started)s
  AND se.ACTUAL_EVENT_AT_UTC IS NOT NULL
  AND o.CUSTOMER_ORDER_NUMBER IS NOT NULL
ORDER BY se.UPDATED_AT_UTC ASC
