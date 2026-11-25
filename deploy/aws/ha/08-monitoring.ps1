# OneWooz optimisation, Phase 7: alerts and a monthly budget.
#
# Usage:  .\08-monitoring.ps1 -Email you@example.com [-BudgetUsd 300]
#
# Creates an SNS topic "onewooz-alerts" with an email subscription (AWS sends a
# confirmation email: click the link or no alert will arrive), 10 CloudWatch
# alarms, and an AWS Budget that emails at 80% of actual spend and at 100% of
# forecast spend. Read-only towards the running app: nothing here can restart
# or change a service. Safe to re-run (alarms and the budget are overwritten).

param(
  [Parameter(Mandatory = $true)][string]$Email,
  [int]$BudgetUsd = 300
)

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$Account = "521831014282"
$Db = "onewoozdb2"
$Redis = "onewooz-redis"
$Asg = "onewooz-ecs-asg"
$Queue = "onewooz-celery"
$AlbDim = "app/onewooz-alb/20f1547cf3af5852"
$TgDim = "targetgroup/onewooz-web/f793e6905c7e5b69"

function Invoke-Aws { param([string[]]$AwsArgs)
  & aws @AwsArgs
  if ($LASTEXITCODE -ne 0) { throw "aws $($AwsArgs[0..1] -join ' ') failed" }
}

# ---- SNS topic ------------------------------------------------------------
$topic = (aws sns create-topic --region $R --name onewooz-alerts --query TopicArn --output text).Trim()
$subs = aws sns list-subscriptions-by-topic --region $R --topic-arn $topic --query "Subscriptions[?Endpoint=='$Email'].SubscriptionArn" --output text
if (-not $subs) {
  Invoke-Aws @("sns", "subscribe", "--region", $R, "--topic-arn", $topic, "--protocol", "email", "--notification-endpoint", $Email) | Out-Null
  Write-Host "Confirmation email sent to $Email. Click the link in it."
}

# Free-memory floor scales with the database size, so the alarm stays useful
# before and after the db.t4g.micro -> db.t4g.small move.
$cls = (aws rds describe-db-instances --region $R --db-instance-identifier $Db --query "DBInstances[0].DBInstanceClass" --output text).Trim()
$memFloor = @{ "db.t4g.micro" = 80MB; "db.t4g.small" = 250MB; "db.t4g.medium" = 500MB }[$cls]
if (-not $memFloor) { $memFloor = 500MB }

function Set-Alarm {
  param($Name, $Desc, $Ns, $Metric, $Dims, $Stat, $Period, $Evals, $Threshold, $Op, $Missing = "notBreaching", $Extended = $null)
  $a = @("cloudwatch", "put-metric-alarm", "--region", $R, "--alarm-name", "onewooz-$Name",
         "--alarm-description", $Desc, "--namespace", $Ns, "--metric-name", $Metric,
         "--period", "$Period", "--evaluation-periods", "$Evals", "--threshold", "$Threshold",
         "--comparison-operator", $Op, "--treat-missing-data", $Missing,
         "--alarm-actions", $topic, "--ok-actions", $topic, "--dimensions") + $Dims
  if ($Extended) { $a += @("--extended-statistic", $Extended) } else { $a += @("--statistic", $Stat) }
  Invoke-Aws $a
  Write-Host "  alarm onewooz-$Name"
}

Write-Host "Creating alarms (database class $cls, memory floor $([int]($memFloor/1MB)) MB)"
Set-Alarm "db-low-memory" "Database free memory low: time to size up" "AWS/RDS" "FreeableMemory" @("Name=DBInstanceIdentifier,Value=$Db") "Average" 300 3 $memFloor "LessThanThreshold"
Set-Alarm "db-high-cpu" "Database CPU above 70% for 15 minutes" "AWS/RDS" "CPUUtilization" @("Name=DBInstanceIdentifier,Value=$Db") "Average" 300 3 70 "GreaterThanThreshold"
Set-Alarm "db-low-disk" "Database free disk below 5 GB" "AWS/RDS" "FreeStorageSpace" @("Name=DBInstanceIdentifier,Value=$Db") "Minimum" 300 1 5368709120 "LessThanThreshold"
Set-Alarm "db-cpu-surcharge" "Database is paying for burst CPU" "AWS/RDS" "CPUSurplusCreditsCharged" @("Name=DBInstanceIdentifier,Value=$Db") "Sum" 3600 1 0 "GreaterThanThreshold"
Set-Alarm "web-5xx" "More than 10 server errors in 5 minutes" "AWS/ApplicationELB" "HTTPCode_Target_5XX_Count" @("Name=LoadBalancer,Value=$AlbDim") "Sum" 300 1 10 "GreaterThanThreshold"
Set-Alarm "web-slow" "p95 response time above 1.5 s for 15 minutes" "AWS/ApplicationELB" "TargetResponseTime" @("Name=LoadBalancer,Value=$AlbDim", "Name=TargetGroup,Value=$TgDim") $null 300 3 1.5 "GreaterThanThreshold" "notBreaching" "p95"
Set-Alarm "jobs-stuck" "Oldest background job waiting over 5 minutes" "AWS/SQS" "ApproximateAgeOfOldestMessage" @("Name=QueueName,Value=$Queue") "Maximum" 300 2 300 "GreaterThanThreshold"
Set-Alarm "redis-memory" "Redis memory above 70%" "AWS/ElastiCache" "DatabaseMemoryUsagePercentage" @("Name=CacheClusterId,Value=$Redis") "Maximum" 300 2 70 "GreaterThanThreshold"
Set-Alarm "servers-cpu-surcharge" "App servers are paying for burst CPU" "AWS/EC2" "CPUSurplusCreditsCharged" @("Name=AutoScalingGroupName,Value=$Asg") "Sum" 3600 1 0 "GreaterThanThreshold"

# Group metrics are free and needed for the "stuck at maximum" alarm.
Invoke-Aws @("autoscaling", "enable-metrics-collection", "--region", $R, "--auto-scaling-group-name", $Asg, "--granularity", "1Minute", "--metrics", "GroupInServiceInstances", "GroupDesiredCapacity", "GroupMaxSize")
$max = (aws autoscaling describe-auto-scaling-groups --region $R --auto-scaling-group-names $Asg --query "AutoScalingGroups[0].MaxSize" --output text).Trim()
Set-Alarm "servers-at-max" "Server group stuck at its maximum ($max) for 15 minutes" "AWS/AutoScaling" "GroupInServiceInstances" @("Name=AutoScalingGroupName,Value=$Asg") "Minimum" 300 3 $max "GreaterThanOrEqualToThreshold"

# Custom-domain edges: the onewooz-edge-failover function reports each edge's
# health every minute. Failover keeps sites up, but a down edge is worth knowing
# about. Missing data (function not running) also alarms.
foreach ($role in @("a", "b")) {
  Set-Alarm "edge-$role-down" "Custom-domain edge $role not answering for 3 minutes (its IP has moved to the other edge)" "OneWooz/Edge" "Healthy" @("Name=Role,Value=$role") "Minimum" 60 3 1 "LessThanThreshold" "breaching"
}

# ---- Budget -------------------------------------------------------------------
$budget = @{
  BudgetName = "onewooz-monthly"; BudgetType = "COST"; TimeUnit = "MONTHLY"
  BudgetLimit = @{ Amount = "$BudgetUsd"; Unit = "USD" }
} | ConvertTo-Json -Compress
$notes = @(
  @{ Notification = @{ NotificationType = "ACTUAL"; ComparisonOperator = "GREATER_THAN"; Threshold = 80; ThresholdType = "PERCENTAGE" }
     Subscribers = @(@{ SubscriptionType = "EMAIL"; Address = $Email }) },
  @{ Notification = @{ NotificationType = "FORECASTED"; ComparisonOperator = "GREATER_THAN"; Threshold = 100; ThresholdType = "PERCENTAGE" }
     Subscribers = @(@{ SubscriptionType = "EMAIL"; Address = $Email }) }
) | ConvertTo-Json -Depth 5 -Compress
$bf = New-TemporaryFile; $nf = New-TemporaryFile
Set-Content -Path $bf -Value $budget -Encoding ascii
Set-Content -Path $nf -Value $notes -Encoding ascii
# Replace an earlier budget of the same name. Listing first avoids a "not found"
# stderr message, which Windows PowerShell 5.1 would turn into a fatal error.
$have = aws budgets describe-budgets --account-id $Account --query "Budgets[?BudgetName=='onewooz-monthly'].BudgetName | [0]" --output text
if ($have -and $have.Trim() -eq "onewooz-monthly") {
  Invoke-Aws @("budgets", "delete-budget", "--account-id", $Account, "--budget-name", "onewooz-monthly")
}
Invoke-Aws @("budgets", "create-budget", "--account-id", $Account, "--budget", "file://$bf", "--notifications-with-subscribers", "file://$nf")
Remove-Item $bf, $nf
Write-Host "Budget onewooz-monthly: `$$BudgetUsd/month, emails at 80% actual and 100% forecast."
Write-Host "Done."
