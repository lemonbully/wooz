# OneWooz optimisation, Phase 3 cutover: custom domains move to the redundant edges.
#
# Before (today)
#   15.252.174.7 -> ECS server i-0bbf9f0e09cec88c2, running the old "edge" Caddy
#   task pinned to that server (a single point of failure).
# After
#   15.252.174.7 -> onewooz-edge-a (zone 1a)    65.1.60.159 -> onewooz-edge-b (zone 1b)
#   Both edges serve every verified domain (certificates are shared in Redis),
#   each replaces itself if it dies, and the onewooz-edge-failover function
#   (every minute) moves an IP to the other edge while one is down.
#
# Already built and tested: both edges serve all custom domains, fixed IP
# 65.1.60.159 is live on edge-b, and a failover drill passed.
#
# What this script does (run at a quiet hour, 02:00 to 04:00 IST):
#   1. Checks both new edges serve a customer domain. Stops if not.
#   2. Allocates a stand-in fixed IP for the old server, so its app tasks
#      (web, beat, marketplace) never lose internet access when 15.252.174.7
#      leaves it.
#   3. Moves 15.252.174.7 to edge-a, then immediately attaches the stand-in IP
#      to the old server. Custom domains blip for about a second.
#   4. Verifies the domains through 15.252.174.7, records edge-a's IP in SSM so
#      self-healing and failover cover it, and stops the old ECS edge task.
#   5. Removes the temporary port-8080 rules for the edges' launch-time IPs.
#
# Rollback (if step 4 fails, the script does this itself):
#   15.252.174.7 goes back to the old server and the old edge task keeps running.
#
# After it succeeds: deploy the web task definition that lists both IPs
# (ONEWOOZ_CUSTOM_DOMAIN_A_RECORDS=15.252.174.7,65.1.60.159) so the Domains page
# asks owners for both A records. The stand-in IP is released when the old
# server is replaced in Phase 4.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$Eip1Alloc = "eipalloc-0795c976765f17746"   # 15.252.174.7
$Eip1 = "15.252.174.7"
$Eip2 = "65.1.60.159"
$AlbSg = "sg-08c63325d9b76a2d5"
$Domains = @("curarich.in", "bamboosoul.co", "herbalmaimaza.com", "mheko.com", "aromalizz.com", "clickcarry.in", "bottlixhouse.com")

function Test-Edge([string]$ip, [string]$domain) {
  $code = & curl.exe -s -o NUL -w "%{http_code}" --max-time 20 --resolve "${domain}:443:${ip}" "https://$domain/"
  return ($code -eq "200")
}

function Get-EdgeA {
  $id = (aws autoscaling describe-auto-scaling-groups --region $R --auto-scaling-group-names onewooz-edge-a `
    --query "AutoScalingGroups[0].Instances[?LifecycleState=='InService'].InstanceId | [0]" --output text).Trim()
  if (-not $id -or $id -eq "None") { throw "edge-a has no InService server" }
  $n = aws ec2 describe-instances --region $R --instance-ids $id `
    --query "Reservations[0].Instances[0].NetworkInterfaces[0].[NetworkInterfaceId,PrivateIpAddress,Association.PublicIp]" --output text
  $p = $n.Trim() -split "\s+"
  return @{ Id = $id; Eni = $p[0]; Primary = $p[1]; Public = $p[2] }
}

# ---- 1. pre-checks ------------------------------------------------------------
$edgeA = Get-EdgeA
Write-Host "edge-a: $($edgeA.Id) private $($edgeA.Primary) public $($edgeA.Public)"
if (-not (Test-Edge $edgeA.Public $Domains[0])) { throw "edge-a does not serve $($Domains[0]); aborting before any change" }
if (-not (Test-Edge $Eip2 $Domains[0])) { throw "edge-b ($Eip2) does not serve $($Domains[0]); aborting before any change" }

$old = aws ec2 describe-addresses --region $R --allocation-ids $Eip1Alloc `
  --query "Addresses[0].[InstanceId,NetworkInterfaceId,PrivateIpAddress]" --output text
$o = $old.Trim() -split "\s+"
$oldInstance = $o[0]; $oldEni = $o[1]; $oldPrivate = $o[2]
if ($oldInstance -eq $edgeA.Id) { Write-Host "$Eip1 is already on edge-a. Nothing to move." ; exit 0 }
Write-Host "$Eip1 currently on $oldInstance ($oldEni $oldPrivate)"

# ---- 2. stand-in IP for the old server ------------------------------------------
$standIn = (aws ec2 allocate-address --region $R --domain vpc `
  --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=onewooz-ecs-standin},{Key=Purpose,Value=ecs-standin}]" `
  --query AllocationId --output text).Trim()
Write-Host "Stand-in IP allocated: $standIn"

# ---- 3. move -------------------------------------------------------------------
aws ec2 associate-address --region $R --allocation-id $Eip1Alloc --network-interface-id $edgeA.Eni `
  --private-ip-address $edgeA.Primary --allow-reassociation | Out-Null
aws ec2 associate-address --region $R --allocation-id $standIn --network-interface-id $oldEni `
  --private-ip-address $oldPrivate | Out-Null
Write-Host "$Eip1 moved to edge-a; stand-in attached to $oldInstance"

# ---- 4. verify, then commit -----------------------------------------------------
Start-Sleep -Seconds 5
$failed = @()
foreach ($d in $Domains) { if (-not (Test-Edge $Eip1 $d)) { $failed += $d } }
if ($failed.Count -gt 0) {
  Write-Warning "Not serving through $Eip1 : $($failed -join ', '). Rolling back."
  aws ec2 associate-address --region $R --allocation-id $Eip1Alloc --network-interface-id $oldEni `
    --private-ip-address $oldPrivate --allow-reassociation | Out-Null
  aws ec2 release-address --region $R --allocation-id $standIn 2>$null | Out-Null
  throw "Rolled back: $Eip1 is on $oldInstance again."
}
Write-Host "All $($Domains.Count) custom domains serve through $Eip1 on edge-a."

aws ssm put-parameter --region $R --name /onewooz/prod/edge/a/eip-alloc --type String --value $Eip1Alloc --overwrite | Out-Null
aws ecs update-service --region $R --cluster onewooz-prod --service edge --desired-count 0 | Out-Null
Write-Host "Old ECS edge task stopped; edge-a is now covered by self-healing and failover."

# ---- 5. tidy the temporary upstream rules ---------------------------------------
$rules = aws ec2 describe-security-group-rules --region $R --filters "Name=group-id,Values=$AlbSg" `
  --query "SecurityGroupRules[?FromPort==``8080`` && (Description=='edge-a-temp-ip' || Description=='edge-b-temp-ip')].SecurityGroupRuleId" --output text
if ($rules -and $rules.Trim()) {
  aws ec2 revoke-security-group-ingress --region $R --group-id $AlbSg --security-group-rule-ids ($rules.Trim() -split "\s+") | Out-Null
  Write-Host "Removed temporary :8080 rules."
}

Write-Host ""
Write-Host "Cutover complete."
Write-Host "  $Eip1 -> edge-a, $Eip2 -> edge-b, failover every minute."
Write-Host "  Next: deploy web with ONEWOOZ_CUSTOM_DOMAIN_A_RECORDS=$Eip1,$Eip2"
