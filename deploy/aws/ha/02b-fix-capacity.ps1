# Fix: AWS had no t4g.medium capacity. Switch the ASG to a mixed-instances policy
# so it can launch any of several ARM64 (Graviton) types across both AZs.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$here = $PSScriptRoot

Write-Host "== Apply mixed-instances policy to the ASG =="
aws autoscaling update-auto-scaling-group --region $R `
  --auto-scaling-group-name onewooz-ecs-asg `
  --mixed-instances-policy "file://$here\mixed-instances.json" | Out-Null
Write-Host "  policy applied (types: t4g/m6g/m7g .medium/.large)"

# Nudge it to retry launching now
aws autoscaling set-desired-capacity --region $R --auto-scaling-group-name onewooz-ecs-asg --desired-capacity 2 | Out-Null
Write-Host "  desired capacity set to 2; instances should launch within ~1-2 min"
Write-Host ""
Write-Host "Check progress:"
Write-Host "  aws autoscaling describe-scaling-activities --region $R --auto-scaling-group-name onewooz-ecs-asg --max-items 2 --query 'Activities[].[StatusCode,StatusMessage]' --output text"
Write-Host "  aws ecs describe-clusters --region $R --clusters onewooz-prod --query 'clusters[0].registeredContainerInstancesCount' --output text"
