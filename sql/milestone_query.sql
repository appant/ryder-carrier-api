-- =============================================================================
-- Milestone puller — incremental pull of recent stop events for Ryder customers
-- Bind parameters:
--   %(cursor_start)s    : timestamp lower bound (last_synced minus overlap)
--   %(run_started)s     : timestamp upper bound (now)
--   %(customer_codes)s  : tuple of customer codes to include
--
-- loadNumber source: ORDER_REFERENCES.VALUE where REFERENCE_TYPE = 'Ship ID'.
-- Orders may have multiple Ship ID rows — pick most-recent by UPDATED_AT_UTC.
-- The inner JOIN on `sid` drops rows without a Ship ID (no fallback).
-- =============================================================================
SELECT
    sid.SHIP_ID,
    se.EVENT_TYPE,
    se.LATE_ARRIVAL_REASON_CODE,
    se.ACTUAL_EVENT_AT_UTC,
    se.ACTUAL_TIMEZONE,
    rs.LOCALITY,
    rs.ADMINISTRATIVE_AREA1_CODE,
    rs.SEQUENCE,
    r.ROUTE_TYPE
FROM STOP_EVENTS se
JOIN ROUTE_STOPS rs ON se.ROUTE_STOP_ID = rs.ROUTE_STOP_ID
JOIN ROUTES r       ON rs.ROUTE_ID = r.ROUTE_ID
JOIN ORDERS o       ON r.LOAD_NUMBER = o.LOAD_NUMBER
JOIN (
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
ORDER BY se.UPDATED_AT_UTC ASC
