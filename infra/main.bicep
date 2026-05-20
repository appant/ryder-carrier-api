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

@description('Cron schedule for the trace job (every 15 min by default)')
param traceCronExpression string = '*/15 * * * *'

@description('Cron schedule for the milestone job (every hour by default)')
param milestoneCronExpression string = '0 * * * *'

@description('Cron schedule for the cleanup job (1st of every month by default)')
param cleanupCronExpression string = '0 0 1 * *'

@description('Container image tag to deploy. Bicep uses the placeholder image until deploy_app.sh runs.')
param imageTag string = 'placeholder'

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
  }
}

// -----------------------------------------------------------------------------
// Container Apps Jobs (trace / milestone / cleanup)
// -----------------------------------------------------------------------------
module jobs 'modules/jobs.bicep' = {
  name: 'jobs'
  params: {
    location: location
    env: env
    jobNamePrefix: jobNamePrefix
    containerAppsEnvId: containerAppsEnv.outputs.id
    uamiId: shared.outputs.uamiId
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
    traceCronExpression: traceCronExpression
    milestoneCronExpression: milestoneCronExpression
    cleanupCronExpression: cleanupCronExpression
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
