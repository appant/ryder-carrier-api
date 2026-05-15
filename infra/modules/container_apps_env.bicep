// Container Apps Environment — the shared compute fabric for all three Jobs.

@description('Region')
param location string

param caeName string

@description('Log Analytics customer ID (workspace ID)')
param lawCustomerId string

@description('Log Analytics primary shared key')
@secure()
param lawPrimarySharedKey string

@description('Application Insights connection string (passed to Jobs via env var)')
param appInsightsConnectionString string

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: caeName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: lawCustomerId
        sharedKey: lawPrimarySharedKey
      }
    }
  }
}

output id                          string = cae.id
output appInsightsConnectionString string = appInsightsConnectionString
