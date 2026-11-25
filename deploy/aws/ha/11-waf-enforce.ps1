# OneWooz: review what the WAF counted, then switch it from count to block.
#
# The firewall (onewooz-alb-waf) was attached on 2026-11-20 in COUNT mode: it
# records what each rule would block but blocks nothing. After about two weeks
# of traffic, check the numbers below. A rule whose counts include real
# visitors (store owners, shoppers, Cashfree/Shiprocket callbacks) should stay
# in count mode.
#
# Usage:
#   .\11-waf-enforce.ps1                      # report only
#   .\11-waf-enforce.ps1 -Apply               # block with every rule
#   .\11-waf-enforce.ps1 -Apply -Rules aws-ip-reputation,aws-known-bad-inputs
#   .\11-waf-enforce.ps1 -Revert              # back to count mode
#
# Rate limits are 2,000 requests per 5 minutes per visitor IP, far above what a
# person browsing or a dashboard tab produces; mobile carriers share IPs, which
# is why the limit is generous.

param([switch]$Apply, [switch]$Revert, [string[]]$Rules = @(), [int]$Days = 14)

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$Name = "onewooz-alb-waf"
$Rules = @($Rules | ForEach-Object { $_ -split "," } | Where-Object { $_ })

$summary = aws wafv2 list-web-acls --region $R --scope REGIONAL --query "WebACLs[?Name=='$Name'] | [0]" --output json | ConvertFrom-Json
if (-not $summary) { throw "web ACL $Name not found" }

# ---- report -------------------------------------------------------------------
$start = (Get-Date).ToUniversalTime().AddDays(-$Days).ToString("yyyy-MM-ddTHH:mm:ssZ")
$end = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$acl = (aws wafv2 get-web-acl --region $R --scope REGIONAL --name $Name --id $summary.Id --output json | ConvertFrom-Json)
Write-Host "Last $Days days, requests each rule matched:"
foreach ($rule in $acl.WebACL.Rules) {
  $metric = $rule.VisibilityConfig.MetricName
  $sum = aws cloudwatch get-metric-statistics --region $R --namespace AWS/WAFV2 --metric-name CountedRequests `
    --dimensions Name=WebACL,Value=$Name Name=Region,Value=$R Name=Rule,Value=$metric `
    --start-time $start --end-time $end --period 86400 --statistics Sum --query "sum(Datapoints[].Sum)" --output text
  $mode = if ($rule.OverrideAction) { if ($rule.OverrideAction.Count) { "count" } else { "block" } } else { if ($rule.Action.Count) { "count" } else { "block" } }
  Write-Host ("  {0,-24} {1,10}   now: {2}" -f $rule.Name, $sum, $mode)
}
Write-Host "Sampled requests: AWS console > WAF > Web ACLs > $Name > Sampled requests."
if (-not $Apply -and -not $Revert) { exit 0 }

# ---- switch -------------------------------------------------------------------
$toBlock = -not $Revert
$changed = @()
foreach ($rule in $acl.WebACL.Rules) {
  if ($Rules.Count -and ($Rules -notcontains $rule.Name)) { continue }
  if ($rule.OverrideAction) {
    if ($toBlock) { $rule.OverrideAction = [pscustomobject]@{ None = [pscustomobject]@{} } } else { $rule.OverrideAction = [pscustomobject]@{ Count = [pscustomobject]@{} } }
  } else {
    if ($toBlock) { $rule.Action = [pscustomobject]@{ Block = [pscustomobject]@{} } } else { $rule.Action = [pscustomobject]@{ Count = [pscustomobject]@{} } }
  }
  $changed += $rule.Name
}
if (-not $changed.Count) { throw "no matching rules" }
$rulesFile = New-TemporaryFile
$acl.WebACL.Rules | ConvertTo-Json -Depth 30 | Set-Content -Path $rulesFile -Encoding ascii
$vis = $acl.WebACL.VisibilityConfig
aws wafv2 update-web-acl --region $R --scope REGIONAL --name $Name --id $summary.Id --lock-token $acl.LockToken `
  --default-action "Allow={}" --rules "file://$rulesFile" `
  --visibility-config "SampledRequestsEnabled=$($vis.SampledRequestsEnabled.ToString().ToLower()),CloudWatchMetricsEnabled=$($vis.CloudWatchMetricsEnabled.ToString().ToLower()),MetricName=$($vis.MetricName)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "update-web-acl failed" }
Remove-Item $rulesFile
Write-Host ("{0}: {1}" -f $(if ($toBlock) { "Now BLOCKING" } else { "Back to COUNT" }), ($changed -join ", "))
foreach ($u in @("https://onewooz.com/", "https://app.onewooz.com/sign-in/", "https://curarich.in/")) {
  $code = & curl.exe -s -o NUL -w "%{http_code}" --max-time 20 $u
  Write-Host ("  {0,-36} {1}" -f $u, $code)
}
