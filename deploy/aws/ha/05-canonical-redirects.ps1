# OneWooz HA  Phase 5: clean canonical redirects for the platform hosts.
#
# Why: the ALB's own redirect actions always write ":443" into the Location
# header (https://onewooz.com:443/...). SEO audits flag it, and it is one more
# variant of every URL. The app now does these redirects itself with a clean
# Location (core.middleware.CanonicalPlatformHostMiddleware), so the ALB only
# has to forward the requests instead of redirecting them:
#
#   :443  rule "www.onewooz.com -> redirect"  becomes  forward to web
#         (app answers 301 https://onewooz.com/<path>)
#   :80   rule "www.onewooz.com -> redirect"  becomes  forward to web, and now
#         also matches onewooz.com, app.onewooz.com and the admin host
#         (app answers 301 https://<host>/<path>)
#
# Everything else on :80 (storefront *.onewooz.site) keeps the listener's
# default HTTP -> HTTPS redirect.
#
# ORDER MATTERS: deploy the app build that contains
# CanonicalPlatformHostMiddleware FIRST, then run this script. Run it from
# PowerShell with AWS credentials for account 521831014282. Safe to re-run.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$here = $PSScriptRoot

$state = @{}
Get-Content "$here\state.env" | Where-Object { $_ -match "^[A-Z0-9_]+=" } | ForEach-Object {
  $k, $v = $_ -split "=", 2; $state[$k] = $v
}
$alb = $state["ALB_ARN"]
$webTg = $state["WEB_TG"]
if (-not $alb -or -not $webTg) { throw "ALB_ARN / WEB_TG missing from state.env" }

$marketing = "onewooz.com"
$platformHosts = @($marketing, "www.$marketing", "app.$marketing", "arcane.$marketing")

function Get-Listener([int]$port) {
  aws elbv2 describe-listeners --region $R --load-balancer-arn $alb `
    --query "Listeners[?Port==``$port``].ListenerArn | [0]" --output text
}

function Get-WwwRule([string]$listener) {
  aws elbv2 describe-rules --region $R --listener-arn $listener `
    --query "Rules[?Conditions[?Field=='host-header' && contains(Values, 'www.$marketing')]].RuleArn | [0]" --output text
}

$forward = "Type=forward,TargetGroupArn=$webTg"

Write-Host "== :443  www rule -> forward to web (app 301s to the apex) =="
$l443 = Get-Listener 443
$r443 = Get-WwwRule $l443
if ($r443 -and $r443 -ne "None") {
  aws elbv2 modify-rule --region $R --rule-arn $r443 --actions $forward | Out-Null
  Write-Host "  modified $r443"
} else {
  Write-Host "  no www rule on :443 (nothing to change)"
}

Write-Host "== :80  platform hosts -> forward to web (app 301s to https) =="
$l80 = Get-Listener 80
$r80 = Get-WwwRule $l80
$hostCond = "Field=host-header,HostHeaderConfig={Values=[" + ($platformHosts -join ",") + "]}"
if ($r80 -and $r80 -ne "None") {
  aws elbv2 modify-rule --region $R --rule-arn $r80 --conditions $hostCond --actions $forward | Out-Null
  Write-Host "  modified $r80"
} else {
  aws elbv2 create-rule --region $R --listener-arn $l80 --priority 1 `
    --conditions $hostCond --actions $forward `
    --tags "Key=Purpose,Value=platform-canonical-redirect" | Out-Null
  Write-Host "  created platform-host rule on :80"
}

Write-Host ""
Write-Host "Check (expect one 301 each, Location without :443):"
Write-Host "  curl -sI http://onewooz.com/            -> https://onewooz.com/"
Write-Host "  curl -sI http://www.onewooz.com/pricing/ -> https://onewooz.com/pricing/"
Write-Host "  curl -sI https://www.onewooz.com/pricing/ -> https://onewooz.com/pricing/"
Write-Host "  curl -sI https://onewooz.com:443/        -> https://onewooz.com/"
