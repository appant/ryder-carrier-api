// =============================================================================
// Ryder Carrier API — main Bicep
//
// Provisions:
//   - Container Registry (Basic)
//   - Storage Account + two tables (watermarks, sentaudit)
//   - Key Vault (RBAC-mode)
//   - User-Assigned Managed Identity (used by the Jobs)
//   - Log Analytics Workspace + Application Insights
//   - Container Apps Environment
//   - 3 Container Apps Jobs (trace / milestone / cleanup) with cron triggers
//   - Role assignments so the UAMI can:
//       * pull from ACR
//       * read secrets from KV
//       * read/write Table Storage
//
// Run via: ./infra/deploy_infra.sh dev | prod
// =============================================================================

targetScope = 'resourceGroup'

@description('Environment name: dev or prod')
@allowed(['dev', 'prod'])
param env string

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Snowflake account identifier (e.g. bj38886.central-us.azure)')
param snowflakeAccount string

@description('Snowflake database (MasterMind share)')
param snowflakeDatabase string

@description('Snowflake warehouse')
param snowflakeWarehouse string = 'COMPUTE_WH'

@description('Snowflake schema')
param snowflakeSchema string = 'PUBLIC'

@description('Snowflake role granted to the service account')
param snowflakeRole string

@description('Snowflake auth method')
@allowed(['password', 'keypair'])
param snowflakeAuthMethod string = 'password'

@description('Ryder Carrier API base URL')
param ryderApiBaseUrl string = 'https://api.ryder.com/rcsc/events/v1'

@description('Comma-separated list of Snowflake CUSTOMER_CODE values to include')
param ryderCustomerCodes string

@description('Cold-start window in minutes: how far back the FIRST run looks when no watermark exists. Applied on cold start only; steady-state runs resume from the watermark. Defaults to 1 (safe: no backfill); each env overrides explicitly (dev=64800, prod=1).')
param watermarkMaxLookbackMinutes int = 1

@description('Overlap buffer in minutes subtracted from the watermark to avoid missing late-arriving rows')
param watermarkOverlapMinutes int = 5

@description('Max minutes a steady-state run looks back once the watermark falls behind (e.g. long outage). Beyond this the oldest gap is skipped (logged as puller_catchup_capped) instead of replayed. Default 1440 = 24h.')
param watermarkMaxCatchupMinutes int = 1440

@description('Audit log retention in days')
param auditRetentionDays int = 180

@description('Max concurrent Ryder API calls per job run')
param ryderMaxConcurrency int = 5

@description('Max retry attempts per Ryder API call')
param ryderMaxRetries int = 5

@description('Ryder API request timeout in seconds')
param ryderTimeoutSeconds int = 30

@description('Outgoing Ryder request rate cap in requests/sec (token bucket). Set to ~80% of the Ryder per-SCAC limit; default 8 assumes a ~10 rps limit. String so it can carry a fractional rate.')
param ryderMaxRps string = '8'

@description('Stop retrying a Ryder call in-process when its Retry-After exceeds this many seconds; defer the row to the next scheduled run instead of blocking a worker.')
param ryderRetryAfterCapSeconds int = 30

@description('Bounded retry: after this many consecutive transient failures of the same row, dead-letter it (blob) instead of stalling the shared watermark. Keep small.')
param ryderMaxTransientAttempts int = 3

@description('Cron schedule for the trace job (every 15 min by default)')
param traceCronExpression string = '*/15 * * * *'

@description('Cron schedule for the milestone job. Runs hourly at :12 — deliberately offset from the trace ticks (:00/:15/:30/:45) so the two Ryder-calling jobs never start simultaneously and burst the shared per-SCAC rate limit.')
param milestoneCronExpression string = '12 * * * *'

@description('Cron schedule for the cleanup job (1st of every month by default)')
param cleanupCronExpression string = '0 0 1 * *'

@description('Container image tag to deploy. Bicep uses the placeholder image until deploy_app.sh runs.')
param imageTag string = 'placeholder'

@description('Email address to notify on alerts (job failures, DLQ, job not running)')
param alertEmail string

// -----------------------------------------------------------------------------
// Tags
// -----------------------------------------------------------------------------
var tags = {
  app: 'ryder'
  Environment: env
  'managed-by': 'bicep'
}

// -----------------------------------------------------------------------------
// Naming
// -----------------------------------------------------------------------------
var suffix       = 'cus-${env}-int-ryder'
var suffixNoDash = 'cus${env}intryder'

var acrName      = 'cr${suffixNoDash}'
var storageName  = 'st${suffixNoDash}'
var kvName       = 'kv-${suffix}'
var uamiName     = 'uami-${suffix}'
var lawName      = 'law-${suffix}'
var appiName     = 'ai-${suffix}'
var caeName      = 'cae-${suffix}'
var jobNamePrefix = 'job-${suffix}'

// -----------------------------------------------------------------------------
// Shared infrastructure
// -----------------------------------------------------------------------------
module shared 'modules/shared.bicep' = {
  name: 'shared'
  params: {
    location: location
    acrName: acrName
    storageName: storageName
    kvName: kvName
    uamiName: uamiName
    lawName: lawName
    appiName: appiName
    tags: tags
  }
}

// -----------------------------------------------------------------------------
// Container Apps Environment
// -----------------------------------------------------------------------------
module containerAppsEnv 'modules/container_apps_env.bicep' = {
  name: 'containerAppsEnv'
  params: {
    location: location
    caeName: caeName
    lawCustomerId: shared.outputs.lawCustomerId
    lawPrimarySharedKey: shared.outputs.lawPrimarySharedKey
    appInsightsConnectionString: shared.outputs.appInsightsConnectionString
    tags: tags
  }
}

// -----------------------------------------------------------------------------
// Container Apps Jobs (trace / milestone / cleanup)
// -----------------------------------------------------------------------------
module jobs 'modules/jobs.bicep' = {
  name: 'jobs'
  params: {
    location: location
    jobNamePrefix: jobNamePrefix
    containerAppsEnvId: containerAppsEnv.outputs.id
    uamiId: shared.outputs.uamiId
    uamiClientId: shared.outputs.uamiClientId
    acrLoginServer: shared.outputs.acrLoginServer
    storageAccountName: storageName
    keyVaultUri: shared.outputs.keyVaultUri
    imageTag: imageTag
    snowflakeAccount: snowflakeAccount
    snowflakeDatabase: snowflakeDatabase
    snowflakeWarehouse: snowflakeWarehouse
    snowflakeSchema: snowflakeSchema
    snowflakeRole: snowflakeRole
    snowflakeAuthMethod: snowflakeAuthMethod
    ryderApiBaseUrl: ryderApiBaseUrl
    ryderCustomerCodes: ryderCustomerCodes
    watermarkMaxLookbackMinutes: watermarkMaxLookbackMinutes
    watermarkOverlapMinutes: watermarkOverlapMinutes
    watermarkMaxCatchupMinutes: watermarkMaxCatchupMinutes
    auditRetentionDays: auditRetentionDays
    ryderMaxConcurrency: ryderMaxConcurrency
    ryderMaxRetries: ryderMaxRetries
    ryderTimeoutSeconds: ryderTimeoutSeconds
    ryderMaxRps: ryderMaxRps
    ryderRetryAfterCapSeconds: ryderRetryAfterCapSeconds
    ryderMaxTransientAttempts: ryderMaxTransientAttempts
    traceCronExpression: traceCronExpression
    milestoneCronExpression: milestoneCronExpression
    cleanupCronExpression: cleanupCronExpression
    tags: tags
  }
}

// -----------------------------------------------------------------------------
// Alerts
// -----------------------------------------------------------------------------
module alerts 'modules/alerts.bicep' = {
  name: 'alerts'
  params: {
    location: location
    lawResourceId: shared.outputs.lawResourceId
    alertEmail: alertEmail
    tags: tags
  }
}

// -----------------------------------------------------------------------------
// Outputs — useful for the app deploy script
// -----------------------------------------------------------------------------
output acrName            string = acrName
output acrLoginServer     string = shared.outputs.acrLoginServer
output storageAccountName string = storageName
output keyVaultName       string = kvName
output uamiId             string = shared.outputs.uamiId
output jobNamePrefix      string = jobNamePrefix
output resourceGroupName  string = resourceGroup().name
