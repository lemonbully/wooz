# OneWooz HA  Phase 4: cut onewooz.com over to the ALB, then delete the old
# single-host stack. Run only after the new web targets are healthy.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$here = $PSScriptRoot
$repo = (Resolve-Path "$here\..\..\..").Path
$OLD_INSTANCE = "i-06291e99471306070"
$OLD_EIP_ALLOC = "eipalloc-0330728176d9053fc"   # 13.204.135.123

Write-Host "== 1. Route53: point platform hosts at the ALB =="
aws route53 change-resource-record-sets --hosted-zone-id Z07446231EMGZMK5P501U `
  --change-batch "file://$here\route53-cutover.json" --query "ChangeInfo.Status" --output text
Write-Host "  onewooz.com / www / app / admin -> ALB (alias). Propagates in ~60s."

Write-Host "== 2. Delete the old single-host services =="
foreach ($svc in @("onewooz-app","onewooz-edge")) {
  try {
    aws ecs delete-service --region $R --cluster onewooz-prod --service $svc --force --query "service.serviceName" --output text
    Write-Host "  deleted $svc"
  } catch { Write-Host "  ($svc already gone)" }
}

Write-Host "== 3. Terminate the old EC2 instance =="
aws ec2 terminate-instances --region $R --instance-ids $OLD_INSTANCE --query "TerminatingInstances[0].CurrentState.Name" --output text
Write-Host "  terminating $OLD_INSTANCE (waiting ~30s before releasing its EIP)"
Start-Sleep -Seconds 30

Write-Host "== 4. Release the old app Elastic IP =="
try {
  aws ec2 release-address --region $R --allocation-id $OLD_EIP_ALLOC
  Write-Host "  released 13.204.135.123"
} catch {
  Write-Host "  EIP still associated; retry in a minute: aws ec2 release-address --region $R --allocation-id $OLD_EIP_ALLOC"
}

Write-Host ""
Write-Host "Phase 4 complete. The site now runs entirely on the ALB + ASG fleet."
Write-Host "Verify:"
Write-Host "  curl -I https://onewooz.com"
Write-Host "  curl -I https://app.onewooz.com/sign-in/"
