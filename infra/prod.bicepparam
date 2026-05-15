using './main.bicep'

param env                = 'prod'
param snowflakeAccount   = 'bj38886.central-us.azure'
// TODO: replace with the prod MasterMind share name once confirmed.
param snowflakeDatabase  = 'MASTERY_USMMG_MASTERMIND_SHARE'
param snowflakeWarehouse = 'COMPUTE_WH'
param snowflakeSchema    = 'PUBLIC'
param snowflakeRole      = 'RYDER_INTEGRATION_ROLE_PROD'
param snowflakeAuthMethod = 'password'
param ryderApiBaseUrl    = 'https://api.ryder.com/rcsc/events/v1'
param ryderCustomerCodes = 'AMEBOTFRTX,DRPEPPFRTX,KEURIGFRTX,KEUDRPFRTX,MOTTSFRTX,UNISHECT,UNISHCT,UNILEVSHCT,UNICADSHCT'
