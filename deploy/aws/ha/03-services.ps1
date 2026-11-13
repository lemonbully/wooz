# OneWooz HA  Phase 3: task defs, ALB site cert, web/worker/beat services, autoscaling.
# Run AFTER Phase 2 instances have registered (registeredContainerInstancesCount >= 2)
# and the onewooz.site ACM cert shows ISSUED.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$here = $PSScriptRoot
$repo = (Resolve-Path "$here\..\..\..").Path
$state = "$here\state.env"
$cfg = @{}; Get-Content $state | Where-Object { $_ -match '=' -and $_ -notmatch '^#' } | ForEach-Object { $k,$v = $_ -split '=',2; $cfg[$k.Trim()] = $v.Trim() }
$albArn = $cfg['ALB_ARN']; $webTg = $cfg['WEB_TG']
$siteCert = "arn:aws:acm:ap-south-1:521831014282:certificate/0dcbb663-cb78-4fd8-b488-af00d1c8f6f3"

Write-Host "== Register task definitions =="
aws ecs register-task-definition --region $R --cli-input-json "file://$here\taskdef-web.json"    --query "taskDefinition.[family,revision]" --output text
aws ecs register-task-definition --region $R --cli-input-json "file://$here\taskdef-worker.json" --query "taskDefinition.[family,revision]" --output text
aws ecs register-task-definition --region $R --cli-input-json "file://$here\taskdef-beat.json"   --query "taskDefinition.[family,revision]" --output text

Write-Host "== Add *.onewooz.site cert to ALB :443 listener (SNI) =="
$rows = aws elbv2 describe-listeners --region $R --load-balancer-arn $albArn --query "Listeners[].[Port,ListenerArn]" --output text
$l443 = (($rows -split "`n") | Where-Object { $_ -match '^\s*443\s' }) -replace '^\s*443\s+','' | Select-Object -First 1
aws elbv2 add-listener-certificates --region $R --listener-arn $l443.Trim() --certificates CertificateArn=$siteCert | Out-Null
Write-Host "  added; ALB now serves onewooz.com + *.onewooz.com + onewooz.site + *.onewooz.site"

Write-Host "== Create services (capacity provider onewooz-cp) =="
# web  behind the ALB target group
aws ecs create-service --region $R --cluster onewooz-prod --service-name web `
  --task-definition onewooz-web --desired-count 2 `
  --capacity-provider-strategy "capacityProvider=onewooz-cp,weight=1" `
  --load-balancers "targetGroupArn=$webTg,containerName=web,containerPort=8000" `
  --health-check-grace-period-seconds 90 `
  --deployment-configuration "maximumPercent=200,minimumHealthyPercent=100" `
  --query "service.serviceName" --output text

aws ecs create-service --region $R --cluster onewooz-prod --service-name worker `
  --task-definition onewooz-worker --desired-count 2 `
  --capacity-provider-strategy "capacityProvider=onewooz-cp,weight=1" `
  --query "service.serviceName" --output text

aws ecs create-service --region $R --cluster onewooz-prod --service-name beat `
  --task-definition onewooz-beat --desired-count 1 `
  --capacity-provider-strategy "capacityProvider=onewooz-cp,weight=1" `
  --deployment-configuration "maximumPercent=100,minimumHealthyPercent=0" `
  --query "service.serviceName" --output text

Write-Host "== Autoscaling (CPU target tracking) =="
# web: 2..6
aws application-autoscaling register-scalable-target --region $R --service-namespace ecs `
  --resource-id service/onewooz-prod/web --scalable-dimension ecs:service:DesiredCount `
  --min-capacity 2 --max-capacity 6 | Out-Null
aws application-autoscaling put-scaling-policy --region $R --service-namespace ecs `
  --resource-id service/onewooz-prod/web --scalable-dimension ecs:service:DesiredCount `
  --policy-name web-cpu60 --policy-type TargetTrackingScaling `
  --target-tracking-scaling-policy-configuration "TargetValue=60,PredefinedMetricSpecification={PredefinedMetricType=ECSServiceAverageCPUUtilization}" | Out-Null
# worker: 1..6
aws application-autoscaling register-scalable-target --region $R --service-namespace ecs `
  --resource-id service/onewooz-prod/worker --scalable-dimension ecs:service:DesiredCount `
  --min-capacity 1 --max-capacity 6 | Out-Null
aws application-autoscaling put-scaling-policy --region $R --service-namespace ecs `
  --resource-id service/onewooz-prod/worker --scalable-dimension ecs:service:DesiredCount `
  --policy-name worker-cpu70 --policy-type TargetTrackingScaling `
  --target-tracking-scaling-policy-configuration "TargetValue=70,PredefinedMetricSpecification={PredefinedMetricType=ECSServiceAverageCPUUtilization}" | Out-Null
Write-Host "  web scales 2-6, worker scales 1-6 on CPU"

Write-Host ""
Write-Host "Phase 3 complete. Watch the web service reach steady state:"
Write-Host "  aws ecs describe-services --region $R --cluster onewooz-prod --services web worker beat --query 'services[].[serviceName,runningCount,desiredCount]' --output text"
Write-Host "  aws elbv2 describe-target-health --region $R --target-group-arn $webTg --query 'TargetHealthDescriptions[].TargetHealth.State' --output text"
Write-Host "When web targets are 'healthy', run Phase 4 (Route53 cutover + teardown old)."
