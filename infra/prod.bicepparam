using './main.bicep'

param env                = 'prod'
param snowflakeAccount   = 'bj38886.central-us.azure'
param snowflakeDatabase  = 'MASTERY_USMMGPROD_MASTERMIND_SHARE'
param snowflakeWarehouse = 'COMPUTE_WH'
param snowflakeSchema    = 'PUBLIC'
param snowflakeRole      = 'RYDER_INTEGRATION_ROLE_PROD'
param snowflakeAuthMethod = 'keypair'
param ryderApiBaseUrl    = 'https://api.ryder.com/rcsc/events/v1'
param ryderCustomerCodes = 'AMEBOTFRTX,DRPEPPFRTX,KEURIGFRTX,KEUDRPFRTX,MOTTSFRTX,ELECTRONOMI'
param watermarkMaxLookbackMinutes = 1
param alertEmail         = 'apant@usmmg.com'
