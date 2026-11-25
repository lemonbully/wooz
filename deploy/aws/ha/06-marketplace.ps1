# OneWooz HA  Phase 6: OneWooz Apps (marketplace.onewooz.com).
#
# Creates, idempotently (safe to re-run):
#   1. SQS queue  onewooz-marketplace        (marketplace Celery work, isolated)
#   2. Target group onewooz-marketplace      (health check /health/live/)
#   3. ALB rules :443 and :80: Host marketplace.onewooz.com -> that target group
#   4. Route53 alias marketplace.onewooz.com -> the ALB (the *.onewooz.com ACM
#      cert on the ALB already covers it)
#   5. Task definitions + ECS services: marketplace (x2, behind the ALB) and
#      worker-marketplace (x1, consumes only the marketplace queue)
#   6. Autoscaling for marketplace (2..4 on CPU 60%)
#
# Run it BEFORE `deploy.ps1 -Services web,worker,beat,marketplace,worker-marketplace`
# so the queue and its worker exist before web/worker/beat start routing to it.
#
#   powershell -ExecutionPolicy Bypass -File deploy\aws\ha\06-marketplace.ps1

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$Cluster = "onewooz-prod"
$Host_ = "marketplace.onewooz.com"
$here = $PSScriptRoot
$cfg = @{}; Get-Content "$here\state.env" | Where-Object { $_ -match '=' -and $_ -notmatch '^#' } | ForEach-Object { $k,$v = $_ -split '=',2; $cfg[$k.Trim()] = $v.Trim() }
$albArn = $cfg['ALB_ARN']; $albDns = $cfg['ALB_DNS']; $albZone = $cfg['ALB_ZONE']; $webTg = $cfg['WEB_TG']

function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

Write-Host "== 1. SQS queue onewooz-marketplace =="
$q = aws sqs create-queue --region $R --queue-name onewooz-marketplace `
  --attributes "VisibilityTimeout=600,ReceiveMessageWaitTimeSeconds=20,MessageRetentionPeriod=345600" `
  --query QueueUrl --output text
Check "create-queue"
Write-Host "  $q"

Write-Host "== 2. Target group onewooz-marketplace =="
$vpc = aws elbv2 describe-target-groups --region $R --target-group-arns $webTg --query "TargetGroups[0].VpcId" --output text
Check "describe web target group"
# Filter the full list rather than `--names`: a missing name is an error on
# stderr, which Windows PowerShell 5.1 turns into a fatal error under "Stop".
$tg = aws elbv2 describe-target-groups --region $R `
  --query "TargetGroups[?TargetGroupName=='onewooz-marketplace'].TargetGroupArn | [0]" --output text
Check "describe target groups"
if (-not $tg -or $tg -eq "None") {
  $tg = aws elbv2 create-target-group --region $R --name onewooz-marketplace --protocol HTTP --port 8000 `
    --vpc-id $vpc --target-type instance --health-check-path /health/live/ `
    --health-check-interval-seconds 15 --healthy-threshold-count 2 --unhealthy-threshold-count 3 `
    --matcher HttpCode=200 --query "TargetGroups[0].TargetGroupArn" --output text
  Check "create-target-group"
}
aws elbv2 modify-target-group-attributes --region $R --target-group-arn $tg `
  --attributes Key=deregistration_delay.timeout_seconds,Value=20 | Out-Null
Write-Host "  $tg"

Write-Host "== 3. ALB host rules (:443 and :80) =="
foreach ($port in 443, 80) {
  $listener = aws elbv2 describe-listeners --region $R --load-balancer-arn $albArn `
    --query "Listeners[?Port==``$port``].ListenerArn | [0]" --output text
  if (-not $listener -or $listener -eq "None") { Write-Host "  no :$port listener, skipping"; continue }
  $existing = aws elbv2 describe-rules --region $R --listener-arn $listener `
    --query "Rules[?Conditions[?Field=='host-header' && contains(Values, '$Host_')]].RuleArn | [0]" --output text
  if ($existing -and $existing -ne "None") { Write-Host "  :$port rule exists"; continue }
  # Lowest free priority between 10 and 99.
  $used = aws elbv2 describe-rules --region $R --listener-arn $listener --query "Rules[].Priority" --output text
  $prio = 10; while (($used -split "\s+") -contains "$prio") { $prio++ }
  aws elbv2 create-rule --region $R --listener-arn $listener --priority $prio `
    --conditions "Field=host-header,Values=$Host_" `
    --actions "Type=forward,TargetGroupArn=$tg" `
    --tags Key=Purpose,Value=marketplace-host | Out-Null
  Check "create-rule :$port"
  Write-Host "  :$port rule created (priority $prio)"
}

Write-Host "== 4. Route53 alias $Host_ -> ALB =="
$zone = (aws route53 list-hosted-zones-by-name --dns-name onewooz.com --query "HostedZones[?Name=='onewooz.com.'].Id | [0]" --output text) -replace '/hostedzone/',''
Check "list-hosted-zones"
$change = @"
{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"$Host_","Type":"A","AliasTarget":{"HostedZoneId":"$albZone","DNSName":"$albDns","EvaluateTargetHealth":true}}}]}
"@
$tmp = New-TemporaryFile
[System.IO.File]::WriteAllText($tmp, $change, (New-Object System.Text.UTF8Encoding($false)))
aws route53 change-resource-record-sets --hosted-zone-id $zone --change-batch "file://$tmp" | Out-Null
Check "route53 upsert"
Remove-Item $tmp
Write-Host "  zone $zone updated"

Write-Host "== 5. Task definitions + services =="
aws ecs register-task-definition --region $R --cli-input-json "file://$here\taskdef-marketplace.json" --query "taskDefinition.[family,revision]" --output text
Check "register marketplace"
aws ecs register-task-definition --region $R --cli-input-json "file://$here\taskdef-worker-marketplace.json" --query "taskDefinition.[family,revision]" --output text
Check "register worker-marketplace"

$have = aws ecs describe-services --region $R --cluster $Cluster --services marketplace worker-marketplace `
  --query "services[?status=='ACTIVE'].serviceName" --output text
if (-not (($have -split "\s+") -contains "marketplace")) {
  aws ecs create-service --region $R --cluster $Cluster --service-name marketplace `
    --task-definition onewooz-marketplace --desired-count 2 `
    --capacity-provider-strategy "capacityProvider=onewooz-cp,weight=1" `
    --load-balancers "targetGroupArn=$tg,containerName=marketplace,containerPort=8000" `
    --health-check-grace-period-seconds 60 `
    --deployment-configuration "maximumPercent=200,minimumHealthyPercent=100" `
    --placement-strategy "type=spread,field=attribute:ecs.availability-zone" `
    --query "service.serviceName" --output text
  Check "create-service marketplace"
} else { Write-Host "  service marketplace exists" }
if (-not (($have -split "\s+") -contains "worker-marketplace")) {
  aws ecs create-service --region $R --cluster $Cluster --service-name worker-marketplace `
    --task-definition onewooz-worker-marketplace --desired-count 1 `
    --capacity-provider-strategy "capacityProvider=onewooz-cp,weight=1" `
    --query "service.serviceName" --output text
  Check "create-service worker-marketplace"
} else { Write-Host "  service worker-marketplace exists" }

Write-Host "== 6. Autoscaling marketplace 2..4 =="
aws application-autoscaling register-scalable-target --region $R --service-namespace ecs `
  --resource-id service/$Cluster/marketplace --scalable-dimension ecs:service:DesiredCount `
  --min-capacity 2 --max-capacity 4 | Out-Null
aws application-autoscaling put-scaling-policy --region $R --service-namespace ecs `
  --resource-id service/$Cluster/marketplace --scalable-dimension ecs:service:DesiredCount `
  --policy-name marketplace-cpu60 --policy-type TargetTrackingScaling `
  --target-tracking-scaling-policy-configuration "TargetValue=60,PredefinedMetricSpecification={PredefinedMetricType=ECSServiceAverageCPUUtilization}" | Out-Null

Write-Host ""
Write-Host "Done. Next: deploy the new image to all five services:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File deploy\aws\ha\deploy.ps1 -Services web,worker,beat,marketplace,worker-marketplace"
