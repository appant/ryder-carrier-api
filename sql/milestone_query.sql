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
  -- Window on the SHARE-ARRIVAL clock, not the source stamp. STOP_EVENTS rows
  -- land in the share minutes-to-hours after their UPDATED_AT_UTC; with the
  -- source-stamp filter, a run can fire before the row is visible, advance the
  -- watermark, and lose it forever (see late-share-load analysis). Filtering on
  -- META_PROCESSED_AT_UTC (when the share ETL actually wrote the row) catches a
  -- record the moment it becomes queryable. Cast LTZ->UTC NTZ so it compares
  -- identically to the source-stamp columns (same bind params + watermark — no
  -- code change). Re-reads under overlap are deduped by the stable natural_key
  -- (load_number + event_type + actual_time), so no duplicate events reach Ryder.
  -- NOTE: this fixes the driving-row-late race. A small subset of misses where a
  -- PARENT (route/order) loads late while the stop event was on-time is NOT fully
  -- covered here — that needs a bounded trailing re-scan backstop (deliberately
  -- NOT GREATEST-of-all-tables, which re-floods on dimension bulk re-stamps).
  AND CONVERT_TIMEZONE('UTC', se.META_PROCESSED_AT_UTC)::TIMESTAMP_NTZ >  %(cursor_start)s
  AND CONVERT_TIMEZONE('UTC', se.META_PROCESSED_AT_UTC)::TIMESTAMP_NTZ <= %(run_started)s
  AND se.ACTUAL_EVENT_AT_UTC IS NOT NULL
ORDER BY se.UPDATED_AT_UTC ASC
