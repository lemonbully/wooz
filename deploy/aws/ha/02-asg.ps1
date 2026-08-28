# OneWooz HA — Phase 2: EC2 launch template, Auto Scaling Group, ECS capacity provider.
# Run AFTER 01-edge-data.ps1. Adds ASG instances (t4g.medium x2, multi-AZ) to cluster onewooz-prod.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R    = "ap-south-1"
$SUBA = "subnet-062eebb731d2e29a9"
$SUBB = "subnet-0d140ce3df79d0163"
$state = "$PSScriptRoot\state.env"
$cfg = @{}; Get-Content $state | Where-Object { $_ -match '=' -and $_ -notmatch '^#' } | ForEach-Object { $k,$v = $_ -split '=',2; $cfg[$k.Trim()] = $v.Trim() }
$instSg = $cfg['INSTANCE_SG']
if (-not $instSg) { throw "INSTANCE_SG not found in state.env - run Phase 1 first" }
Write-Host "Using INSTANCE_SG = $instSg"

Write-Host "== Launch template =="
$userData = "#!/bin/bash`necho ECS_CLUSTER=onewooz-prod >> /etc/ecs/ecs.config`necho ECS_ENABLE_SPOT_INSTANCE_DRAINING=true >> /etc/ecs/ecs.config`n"
$ud64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($userData))
$ltData = @{
  ImageId = "resolve:ssm:/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id"
  InstanceType = "t4g.medium"
  IamInstanceProfile = @{ Name = "ecsInstanceRole" }
  UserData = $ud64
  NetworkInterfaces = @(@{ DeviceIndex = 0; AssociatePublicIpAddress = $true; Groups = @($instSg) })
  TagSpecifications = @(@{ ResourceType = "instance"; Tags = @(@{ Key = "Name"; Value = "onewooz-ecs" }) })
}
$ltFile = "$PSScriptRoot\lt-data.json"
($ltData | ConvertTo-Json -Depth 8) | Out-File -Encoding ascii $ltFile
aws ec2 create-launch-template --region $R --launch-template-name onewooz-ecs --launch-template-data "file://$ltFile" --query "LaunchTemplate.LaunchTemplateId" --output text
Write-Host "  launch template onewooz-ecs created"

Write-Host "== Auto Scaling Group (min 2, max 4, 2 AZs) =="
aws autoscaling create-auto-scaling-group --region $R `
  --auto-scaling-group-name onewooz-ecs-asg `
  --launch-template "LaunchTemplateName=onewooz-ecs,Version=`$Latest" `
  --min-size 2 --max-size 4 --desired-capacity 2 `
  --vpc-zone-identifier "$SUBA,$SUBB" `
  --health-check-type EC2 --health-check-grace-period 120 | Out-Null
$asgArn = aws autoscaling describe-auto-scaling-groups --region $R --auto-scaling-group-names onewooz-ecs-asg --query "AutoScalingGroups[0].AutoScalingGroupARN" --output text
Write-Host "  ASG onewooz-ecs-asg created"

Write-Host "== ECS capacity provider =="
aws ecs create-capacity-provider --region $R --name onewooz-cp `
  --auto-scaling-group-provider "autoScalingGroupArn=$asgArn,managedScaling={status=ENABLED,targetCapacity=100},managedTerminationProtection=DISABLED" | Out-Null
aws ecs put-cluster-capacity-providers --region $R --cluster onewooz-prod `
  --capacity-providers onewooz-cp `
  --default-capacity-provider-strategy "capacityProvider=onewooz-cp,weight=1" | Out-Null
Write-Host "  capacity provider onewooz-cp attached to cluster onewooz-prod"

Write-Host ""
Write-Host "Phase 2 complete. Waiting for 2 instances to register with the cluster..."
Write-Host "Check with:"
Write-Host "  aws ecs describe-clusters --region $R --clusters onewooz-prod --query 'clusters[0].registeredContainerInstancesCount'"
