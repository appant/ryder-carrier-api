// =============================================================================
// Ryder Carrier API — Alerts
//
// Creates an Action Group (email) + 4 Scheduled Query Rules:
//   1. job_failed         — any job crashed
//   2. row_failed_permanently — Ryder rejected a row (DLQ)
//   3. no_data_sent       — job ran but sent 0 rows despite seeing data
//   4. job_not_running    — milestone/trace hasn't completed in expected window
// =============================================================================

param location string
param lawResourceId string       // Log Analytics workspace resource ID
param alertEmail string          // Who to notify
param tags object = {}

// -----------------------------------------------------------------------------
// Action Group — email on every alert
// -----------------------------------------------------------------------------
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-ryder-alerts'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'RyderAlert'
    enabled: true
    emailReceivers: [
      {
        name: 'ops-email'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// -----------------------------------------------------------------------------
// 1. Job failed — fires within 5 min of any job_failed log
// -----------------------------------------------------------------------------
resource alertJobFailed 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-ryder-job-failed'
  location: location
  tags: tags
  properties: {
    displayName: 'Ryder — Job Failed'
    description: 'A trace, milestone, or cleanup job crashed. Check Log Analytics for the stack trace.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    scopes: [lawResourceId]
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where ContainerName_s in ("milestone", "trace", "cleanup")
| where Log_s has "job_failed"
| summarize count()
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
    autoMitigate: true
  }
}

// -----------------------------------------------------------------------------
// 2. Row failed permanently (DLQ) — Ryder rejected a row
// -----------------------------------------------------------------------------
resource alertDlq 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-ryder-dlq'
  location: location
  tags: tags
  properties: {
    displayName: 'Ryder — Row Rejected by Ryder (DLQ)'
    description: 'Ryder returned a permanent error for one or more rows. Check response_body in logs for the rejection reason.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT15M'
    windowSize: 'PT30M'
    scopes: [lawResourceId]
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where ContainerName_s in ("milestone", "trace")
| where Log_s has "row_failed_permanently"
| summarize count()
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
    autoMitigate: true
  }
}

// -----------------------------------------------------------------------------
// 3. No data sent — job pulled rows but sent none (mapping/transform issue)
// -----------------------------------------------------------------------------
resource alertNoDataSent 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-ryder-no-data-sent'
  location: location
  tags: tags
  properties: {
    displayName: 'Ryder — Job Pulled Data But Sent Nothing'
    description: 'A job saw rows from Snowflake but rows_sent=0. Could indicate a transform bug or all rows invalid.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT30M'
    windowSize: 'PT1H'
    scopes: [lawResourceId]
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where ContainerName_s in ("milestone", "trace")
| where Log_s has "puller_run_complete"
| extend rows_seen = toint(extract('"rows_seen": (\\d+)', 1, Log_s))
| extend rows_sent = toint(extract('"rows_sent": (\\d+)', 1, Log_s))
| where rows_seen > 0 and rows_sent == 0
| summarize count()
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
    autoMitigate: true
  }
}

// -----------------------------------------------------------------------------
// 4. Job not running — no puller_run_complete in expected window
//    Milestone runs hourly → alert if silent for 2h
//    Trace runs every 15min → alert if silent for 45min
// -----------------------------------------------------------------------------
resource alertMilestoneNotRunning 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-ryder-milestone-not-running'
  location: location
  tags: tags
  properties: {
    displayName: 'Ryder — Milestone Job Not Running'
    description: 'No milestone puller_run_complete in the last 2 hours. Job may be stuck or not scheduled.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT30M'
    windowSize: 'PT2H'
    scopes: [lawResourceId]
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where ContainerName_s == "milestone"
| where Log_s has "puller_run_complete"
| summarize count()
'''
          timeAggregation: 'Count'
          operator: 'LessThan'
          threshold: 1
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
    autoMitigate: true
  }
}

resource alertTraceNotRunning 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-ryder-trace-not-running'
  location: location
  tags: tags
  properties: {
    displayName: 'Ryder — Trace Job Not Running'
    description: 'No trace puller_run_complete in the last 45 minutes. Job may be stuck or not scheduled.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT15M'
    windowSize: 'PT45M'
    scopes: [lawResourceId]
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where ContainerName_s == "trace"
| where Log_s has "puller_run_complete"
| summarize count()
'''
          timeAggregation: 'Count'
          operator: 'LessThan'
          threshold: 1
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
    autoMitigate: true
  }
}
