// Three Container Apps Jobs (trace / milestone / cleanup) sharing one image.
//
// On first deploy, image is a placeholder (`mcr.microsoft.com/k8se/quickstart-jobs`).
// deploy_app.sh then pushes the real image to ACR and updates each job's image
// reference. After that, cron triggers fire the real container on schedule.

@description('Region')
param location string
param tags     object = {}


@description('Prefix for the 3 job names (e.g. job-cus-dev-int-ryder)')
param jobNamePrefix string

param containerAppsEnvId string
param uamiId             string
param uamiClientId       string
param acrLoginServer     string
param storageAccountName string
param keyVaultUri        string
param imageTag           string

param snowflakeAccount    string
param snowflakeDatabase   string
param snowflakeWarehouse  string
param snowflakeSchema     string
param snowflakeRole       string
param snowflakeAuthMethod string

param ryderApiBaseUrl    string
param ryderCustomerCodes string

param watermarkMaxLookbackMinutes int = 1
param watermarkOverlapMinutes   int = 5
param watermarkMaxCatchupMinutes int = 1440
param auditRetentionDays        int = 180
param ryderMaxConcurrency       int = 5
param ryderMaxRetries           int = 5
param ryderTimeoutSeconds       int = 30
param ryderMaxRps               string = '8'
param ryderRetryAfterCapSeconds int = 30
param ryderMaxTransientAttempts int = 3

param traceCronExpression     string
param milestoneCronExpression string
param cleanupCronExpression   string

// -----------------------------------------------------------------------------
// Placeholder vs. real image — Bicep's first run uses the public quickstart
// image so the job can be created; deploy_app.sh replaces it.
// -----------------------------------------------------------------------------
var placeholderImage = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'
var realImage        = '${acrLoginServer}/ryder-carrier-api:${imageTag}'
var image            = imageTag == 'placeholder' ? placeholderImage : realImage

// -----------------------------------------------------------------------------
// Common environment variables for all three jobs.
// Secrets are pulled from Key Vault via the UAMI at runtime (no values in the
// Job definition — Key Vault references would also work; here we use plain
// vars and let the app's SecretProvider talk to KV directly).
// -----------------------------------------------------------------------------
var commonEnvVars = [
  { name: 'APP_ENV',                 value: 'prod' }
  { name: 'AZURE_CLIENT_ID',        value: uamiClientId }
  { name: 'LOG_LEVEL',               value: 'INFO' }
  { name: 'KEY_VAULT_URI',           value: keyVaultUri }
  { name: 'SECRETS_BLOB_URL',        value: '' }
  { name: 'STORAGE_ACCOUNT_NAME',    value: storageAccountName }
  { name: 'STORAGE_CONNECTION_STRING', value: '' }
  { name: 'SNOWFLAKE_AUTH_METHOD',   value: snowflakeAuthMethod }
  { name: 'SNOWFLAKE_ACCOUNT',       value: snowflakeAccount }
  { name: 'SNOWFLAKE_DATABASE',      value: snowflakeDatabase }
  { name: 'SNOWFLAKE_WAREHOUSE',     value: snowflakeWarehouse }
  { name: 'SNOWFLAKE_SCHEMA',        value: snowflakeSchema }
  { name: 'SNOWFLAKE_ROLE',          value: snowflakeRole }
  { name: 'RYDER_API_BASE_URL',               value: ryderApiBaseUrl }
  { name: 'RYDER_CUSTOMER_CODES',             value: ryderCustomerCodes }
  { name: 'RYDER_MAX_CONCURRENCY',            value: string(ryderMaxConcurrency) }
  { name: 'RYDER_MAX_RETRIES',                value: string(ryderMaxRetries) }
  { name: 'RYDER_TIMEOUT_SECONDS',            value: string(ryderTimeoutSeconds) }
  { name: 'RYDER_MAX_RPS',                    value: ryderMaxRps }
  { name: 'RYDER_RETRY_AFTER_CAP_SECONDS',    value: string(ryderRetryAfterCapSeconds) }
  { name: 'RYDER_MAX_TRANSIENT_ATTEMPTS',     value: string(ryderMaxTransientAttempts) }
  { name: 'WATERMARK_MAX_LOOKBACK_MINUTES',   value: string(watermarkMaxLookbackMinutes) }
  { name: 'WATERMARK_OVERLAP_MINUTES',        value: string(watermarkOverlapMinutes) }
  { name: 'WATERMARK_MAX_CATCHUP_MINUTES',    value: string(watermarkMaxCatchupMinutes) }
  { name: 'AUDIT_RETENTION_DAYS',             value: string(auditRetentionDays) }
]

var registriesConfig = [
  {
    server: acrLoginServer
    identity: uamiId
  }
]

// -----------------------------------------------------------------------------
// Helper: build a Job resource. Each job differs only in name, args, and cron.
// -----------------------------------------------------------------------------
resource traceJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${jobNamePrefix}-trace'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvId
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: traceCronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 600
      replicaRetryLimit: 0
      registries: registriesConfig
    }
    template: {
      containers: [
        {
          name: 'trace'
          image: image
          args: ['trace']
          env: commonEnvVars
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

resource milestoneJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${jobNamePrefix}-milestone'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvId
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: milestoneCronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 1200
      replicaRetryLimit: 0
      registries: registriesConfig
    }
    template: {
      containers: [
        {
          name: 'milestone'
          image: image
          args: ['milestone']
          env: commonEnvVars
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

resource cleanupJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${jobNamePrefix}-cleanup'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvId
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: cleanupCronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 1800
      replicaRetryLimit: 0
      registries: registriesConfig
    }
    template: {
      containers: [
        {
          name: 'cleanup'
          image: image
          args: ['cleanup']
          env: commonEnvVars
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}

output traceJobName     string = traceJob.name
output milestoneJobName string = milestoneJob.name
output cleanupJobName   string = cleanupJob.name
