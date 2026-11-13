<#
OneWooz CloudFront setup -- cdn.onewooz.com (static + media) and the storefronts.

Creates, idempotently (safe to re-run; every step looks for what already exists):

  A. cdn.onewooz.com  -- one distribution in front of BOTH S3 buckets
       /static/*  -> onewooz-static-bucket   (content-hashed CSS/JS/fonts, cached 1 year)
       /*         -> onewooz-media-bucket    (owner uploads, cached 1-30 days)
     Private S3 access via Origin Access Control, Brotli/gzip, HTTP/2+3, CORS for fonts.

  B. *.onewooz.site + onewooz.site -- one distribution in front of the ALB
     Every storefront request goes through the nearest CloudFront edge (TLS ends
     close to the shopper, AWS backbone to Mumbai, Brotli). Responses are cached
     ONLY when Django says they are shareable (Cache-Control: public / max-age):
     robots.txt, favicon, price-history JSON. Pages carrying a visitor's cookies
     (cart, CSRF, login, analytics) are never cached -- Django's
     core.middleware.EdgeDeliveryMiddleware enforces that. Cache key includes the
     Host header, so one store can never be served another store's response.

Run from the repo root, in PowerShell, with the same AWS credentials you use
for deploy.ps1:

    powershell -ExecutionPolicy Bypass -File deploy\aws\cdn\setup-cloudfront.ps1

Only the CDN part:            ... -SkipStorefront
Only the storefront part:     ... -SkipCdn

Then deploy the app (the task definitions already carry
ONEWOOZ_STATIC_CDN_DOMAIN / ONEWOOZ_MEDIA_CDN_DOMAIN = cdn.onewooz.com):

    powershell -ExecutionPolicy Bypass -File deploy\aws\ha\deploy.ps1

Order matters: run THIS script first. Deploying the app before cdn.onewooz.com
resolves would point every CSS/JS URL at a host that does not exist yet.
#>
[CmdletBinding()]
param(
    [string] $Region         = "ap-south-1",
    [string] $Account        = "521831014282",
    [string] $CdnHost        = "cdn.onewooz.com",
    [string] $CdnZone        = "onewooz.com",
    [string] $StaticBucket   = "onewooz-static-bucket",
    [string] $MediaBucket    = "onewooz-media-bucket",
    [string] $StorefrontApex = "onewooz.site",
    [string] $AlbName        = "onewooz-alb",
    [switch] $SkipCdn,
    [switch] $SkipStorefront
)

$ErrorActionPreference = "Stop"

# CloudFront constants (AWS-managed, identical in every account).
$CLOUDFRONT_ZONE_ID          = "Z2FDTNDATAQYW2"                        # alias target zone for every distribution
$POLICY_CACHING_OPTIMIZED    = "658327ea-f89d-4fab-a63d-7e88639e58f6"  # Managed-CachingOptimized
$POLICY_USE_ORIGIN_HEADERS_QS = "4cc15a8a-d715-48a4-82b8-cc0b614638fe" # Managed-UseOriginCacheControlHeaders-QueryStrings
$ORP_ALL_VIEWER_AND_CF       = "33f36d7e-f396-46d9-90e0-52428a34d9dc"  # Managed-AllViewerAndCloudFrontHeaders-2022-06
$RHP_SIMPLE_CORS             = "60669652-455b-4ae9-85a4-c4c02393f86c"  # Managed-SimpleCORS

$OAC_NAME          = "onewooz-s3-oac"
$MEDIA_POLICY_NAME = "OneWooz-Media"

$work = Join-Path $env:TEMP "onewooz-cloudfront"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

function Invoke-Aws([string[]] $Arguments, [switch] $AllowFailure) {
    $out = & aws @Arguments --output json
    if ($LASTEXITCODE -ne 0) {
        if ($AllowFailure) { return $null }
        throw "aws $($Arguments -join ' ') failed (exit $LASTEXITCODE)"
    }
    $text = ($out -join "`n").Trim()
    if (-not $text) { return $null }
    return $text | ConvertFrom-Json
}

function Write-JsonFile([string] $Name, $Object) {
    $path = Join-Path $work $Name
    [System.IO.File]::WriteAllText($path, ($Object | ConvertTo-Json -Depth 30), $utf8NoBom)
    return "file://$path"
}

function Step([string] $Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Ok([string] $Text)   { Write-Host "    $Text" -ForegroundColor Green }

function Get-ZoneId([string] $DomainName) {
    $res = Invoke-Aws @("route53", "list-hosted-zones-by-name", "--dns-name", $DomainName)
    $zone = $res.HostedZones | Where-Object { $_.Name -eq "$DomainName." -and -not $_.Config.PrivateZone } | Select-Object -First 1
    if (-not $zone) { throw "No public Route53 hosted zone for $DomainName" }
    return ($zone.Id -replace "^/hostedzone/", "")
}

# Request (or reuse) a us-east-1 ACM certificate -- CloudFront only accepts
# certificates from us-east-1 -- write its DNS validation CNAMEs into Route53 and
# wait for it to be issued.
function Get-CloudFrontCertificate([string] $Primary, [string[]] $Alternates, [string] $ZoneId) {
    $list = Invoke-Aws @("acm", "list-certificates", "--region", "us-east-1",
                         "--certificate-statuses", "ISSUED", "PENDING_VALIDATION")
    $existing = $list.CertificateSummaryList | Where-Object { $_.DomainName -eq $Primary } | Select-Object -First 1
    if ($existing) {
        $arn = $existing.CertificateArn
        Ok "reusing certificate $arn"
    }
    else {
        $req = @("acm", "request-certificate", "--region", "us-east-1",
                 "--domain-name", $Primary, "--validation-method", "DNS",
                 "--idempotency-token", ("ow" + ($Primary -replace "[^a-zA-Z0-9]", "")).Substring(0, [Math]::Min(32, 2 + ($Primary -replace "[^a-zA-Z0-9]", "").Length)))
        if ($Alternates) { $req += @("--subject-alternative-names") + $Alternates }
        $arn = (Invoke-Aws $req).CertificateArn
        Ok "requested certificate $arn"
    }

    # The validation records appear a few seconds after the request.
    $options = $null
    for ($i = 0; $i -lt 30; $i++) {
        $cert = (Invoke-Aws @("acm", "describe-certificate", "--region", "us-east-1", "--certificate-arn", $arn)).Certificate
        if ($cert.Status -eq "ISSUED") { Ok "certificate already ISSUED"; return $arn }
        $options = @($cert.DomainValidationOptions | Where-Object { $_.ResourceRecord })
        if ($options.Count -ge 1 -and $options.Count -eq @($cert.DomainValidationOptions).Count) { break }
        Start-Sleep -Seconds 4
    }
    if (-not $options) { throw "ACM did not return validation records for $Primary" }

    $changes = @()
    $seen = @{}
    foreach ($o in $options) {
        $rr = $o.ResourceRecord
        if ($seen.ContainsKey($rr.Name)) { continue }   # apex + wildcard share one record
        $seen[$rr.Name] = $true
        $changes += @{
            Action = "UPSERT"
            ResourceRecordSet = @{
                Name = $rr.Name; Type = $rr.Type; TTL = 300
                ResourceRecords = @(@{ Value = $rr.Value })
            }
        }
    }
    $batch = Write-JsonFile "acm-$($Primary -replace '[^a-zA-Z0-9]', '-').json" @{ Comment = "ACM validation for $Primary"; Changes = $changes }
    Invoke-Aws @("route53", "change-resource-record-sets", "--hosted-zone-id", $ZoneId, "--change-batch", $batch) | Out-Null
    Ok "validation records written; waiting for ACM to issue (usually 1-5 min)..."
    & aws acm wait certificate-validated --region us-east-1 --certificate-arn $arn
    if ($LASTEXITCODE -ne 0) { throw "certificate $arn was not validated -- check the CNAMEs in Route53 and re-run" }
    Ok "certificate ISSUED"
    return $arn
}

function Find-DistributionByAlias([string] $Alias) {
    $res = Invoke-Aws @("cloudfront", "list-distributions")
    if (-not $res.DistributionList.Items) { return $null }
    return $res.DistributionList.Items |
        Where-Object { $_.Aliases.Items -and ($_.Aliases.Items -contains $Alias) } |
        Select-Object -First 1
}

function Set-AliasRecords([string] $ZoneId, [string[]] $Names, [string] $CloudFrontDomain) {
    $changes = @()
    foreach ($n in $Names) {
        foreach ($t in @("A", "AAAA")) {
            $changes += @{
                Action = "UPSERT"
                ResourceRecordSet = @{
                    Name = $n; Type = $t
                    AliasTarget = @{ HostedZoneId = $CLOUDFRONT_ZONE_ID; DNSName = $CloudFrontDomain; EvaluateTargetHealth = $false }
                }
            }
        }
    }
    $batch = Write-JsonFile "dns-$($Names[0] -replace '[^a-zA-Z0-9]', '-').json" @{ Comment = "Point $($Names -join ', ') at CloudFront"; Changes = $changes }
    Invoke-Aws @("route53", "change-resource-record-sets", "--hosted-zone-id", $ZoneId, "--change-batch", $batch) | Out-Null
    Ok "Route53: $($Names -join ', ') -> $CloudFrontDomain (A + AAAA)"
}

# Add a CloudFront read grant to a bucket policy WITHOUT dropping the
# statements already there (the public-read grant on static/* stays, so pages
# cached with the old S3 URLs keep working during the cut-over).
function Grant-CloudFrontRead([string] $Bucket, [string] $Resource, [string] $DistributionArn, [string] $Sid) {
    $current = Invoke-Aws @("s3api", "get-bucket-policy", "--bucket", $Bucket) -AllowFailure
    if ($current -and $current.Policy) {
        $statements = @(($current.Policy | ConvertFrom-Json).Statement | Where-Object { $_.Sid -ne $Sid })
    }
    else {
        $statements = @()
    }
    $statements += [pscustomobject]@{
        Sid       = $Sid
        Effect    = "Allow"
        Principal = @{ Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = $Resource
        Condition = @{ StringEquals = @{ "AWS:SourceArn" = $DistributionArn } }
    }
    $doc = [ordered]@{ Version = "2012-10-17"; Statement = $statements }
    $file = Write-JsonFile "policy-$Bucket.json" $doc
    Invoke-Aws @("s3api", "put-bucket-policy", "--bucket", $Bucket, "--policy", $file) | Out-Null
    Ok "bucket policy on $Bucket now allows CloudFront ($Sid)"
}

# ---------------------------------------------------------------------------
# A. cdn.onewooz.com
# ---------------------------------------------------------------------------

if (-not $SkipCdn) {
    Step "[CDN] Route53 zone for $CdnZone"
    $cdnZoneId = Get-ZoneId $CdnZone
    Ok $cdnZoneId

    Step "[CDN] ACM certificate (us-east-1) for $CdnHost"
    $cdnCertArn = Get-CloudFrontCertificate -Primary $CdnHost -Alternates @() -ZoneId $cdnZoneId

    Step "[CDN] Origin Access Control '$OAC_NAME'"
    $oacs = Invoke-Aws @("cloudfront", "list-origin-access-controls")
    $oac = $oacs.OriginAccessControlList.Items | Where-Object { $_.Name -eq $OAC_NAME } | Select-Object -First 1
    if ($oac) { $oacId = $oac.Id; Ok "reusing $oacId" }
    else {
        $oacFile = Write-JsonFile "oac.json" @{
            Name = $OAC_NAME
            Description = "OneWooz CloudFront -> private S3 (static + media)"
            SigningProtocol = "sigv4"; SigningBehavior = "always"; OriginAccessControlOriginType = "s3"
        }
        $oacId = (Invoke-Aws @("cloudfront", "create-origin-access-control", "--origin-access-control-config", $oacFile)).OriginAccessControl.Id
        Ok "created $oacId"
    }

    Step "[CDN] Cache policy '$MEDIA_POLICY_NAME' (uploads: 1 day min, 7 days default, 1 year max)"
    $policies = Invoke-Aws @("cloudfront", "list-cache-policies", "--type", "custom")
    $mp = $policies.CachePolicyList.Items | Where-Object { $_.CachePolicy.CachePolicyConfig.Name -eq $MEDIA_POLICY_NAME } | Select-Object -First 1
    if ($mp) { $mediaPolicyId = $mp.CachePolicy.Id; Ok "reusing $mediaPolicyId" }
    else {
        $mpFile = Write-JsonFile "media-cache-policy.json" @{
            Name = $MEDIA_POLICY_NAME
            Comment = "OneWooz uploads: keys are never overwritten, so cache long"
            MinTTL = 86400; DefaultTTL = 604800; MaxTTL = 31536000
            ParametersInCacheKeyAndForwardedToOrigin = @{
                EnableAcceptEncodingGzip = $true
                EnableAcceptEncodingBrotli = $true
                HeadersConfig = @{ HeaderBehavior = "none" }
                CookiesConfig = @{ CookieBehavior = "none" }
                QueryStringsConfig = @{ QueryStringBehavior = "none" }
            }
        }
        $mediaPolicyId = (Invoke-Aws @("cloudfront", "create-cache-policy", "--cache-policy-config", $mpFile)).CachePolicy.Id
        Ok "created $mediaPolicyId"
    }

    Step "[CDN] Distribution for $CdnHost"
    $cdnDist = Find-DistributionByAlias $CdnHost
    if ($cdnDist) {
        $cdnId = $cdnDist.Id; $cdnDomain = $cdnDist.DomainName; $cdnArn = $cdnDist.ARN
        Ok "reusing $cdnId ($cdnDomain)"
    }
    else {
        $s3Methods = @{
            Quantity = 3; Items = @("GET", "HEAD", "OPTIONS")
            CachedMethods = @{ Quantity = 3; Items = @("GET", "HEAD", "OPTIONS") }
        }
        $cdnConfig = @{
            CallerReference = "onewooz-cdn-" + (Get-Date -Format "yyyyMMddHHmmss")
            Comment = "OneWooz CDN: $CdnHost (static + media)"
            Enabled = $true
            Aliases = @{ Quantity = 1; Items = @($CdnHost) }
            PriceClass = "PriceClass_200"
            HttpVersion = "http2and3"
            IsIPV6Enabled = $true
            DefaultRootObject = ""
            Origins = @{
                Quantity = 2
                Items = @(
                    @{
                        Id = "s3-static"
                        DomainName = "$StaticBucket.s3.$Region.amazonaws.com"
                        OriginPath = ""
                        S3OriginConfig = @{ OriginAccessIdentity = "" }
                        OriginAccessControlId = $oacId
                    },
                    @{
                        Id = "s3-media"
                        DomainName = "$MediaBucket.s3.$Region.amazonaws.com"
                        OriginPath = ""
                        S3OriginConfig = @{ OriginAccessIdentity = "" }
                        OriginAccessControlId = $oacId
                    }
                )
            }
            # Uploads (default): long edge cache, Brotli/gzip, CORS for <video>/fetch.
            DefaultCacheBehavior = @{
                TargetOriginId = "s3-media"
                ViewerProtocolPolicy = "redirect-to-https"
                AllowedMethods = $s3Methods
                Compress = $true
                CachePolicyId = $mediaPolicyId
                ResponseHeadersPolicyId = $RHP_SIMPLE_CORS
            }
            # Collected static: content-hashed + "immutable", cached for a year.
            CacheBehaviors = @{
                Quantity = 1
                Items = @(
                    @{
                        PathPattern = "/static/*"
                        TargetOriginId = "s3-static"
                        ViewerProtocolPolicy = "redirect-to-https"
                        AllowedMethods = $s3Methods
                        Compress = $true
                        CachePolicyId = $POLICY_CACHING_OPTIMIZED
                        ResponseHeadersPolicyId = $RHP_SIMPLE_CORS
                    }
                )
            }
            ViewerCertificate = @{
                ACMCertificateArn = $cdnCertArn
                SSLSupportMethod = "sni-only"
                MinimumProtocolVersion = "TLSv1.2_2021"
            }
        }
        $file = Write-JsonFile "cdn-distribution.json" $cdnConfig
        $created = (Invoke-Aws @("cloudfront", "create-distribution", "--distribution-config", $file)).Distribution
        $cdnId = $created.Id; $cdnDomain = $created.DomainName; $cdnArn = $created.ARN
        Ok "created $cdnId ($cdnDomain)"
    }

    Step "[CDN] S3 bucket policies (Origin Access Control)"
    Grant-CloudFrontRead -Bucket $StaticBucket -Resource "arn:aws:s3:::$StaticBucket/static/*" -DistributionArn $cdnArn -Sid "AllowOneWoozCdnRead"
    Grant-CloudFrontRead -Bucket $MediaBucket  -Resource "arn:aws:s3:::$MediaBucket/*"         -DistributionArn $cdnArn -Sid "AllowOneWoozCdnRead"

    Step "[CDN] DNS $CdnHost -> CloudFront"
    Set-AliasRecords -ZoneId $cdnZoneId -Names @($CdnHost) -CloudFrontDomain $cdnDomain

    Step "[CDN] Waiting for the distribution to deploy (5-15 min)..."
    & aws cloudfront wait distribution-deployed --id $cdnId
    if ($LASTEXITCODE -ne 0) { Write-Warning "still deploying -- it finishes on its own; check: aws cloudfront get-distribution --id $cdnId" }
    else { Ok "https://$CdnHost is live" }
}

# ---------------------------------------------------------------------------
# B. Storefronts: *.onewooz.site -> CloudFront -> ALB
# ---------------------------------------------------------------------------

if (-not $SkipStorefront) {
    Step "[Storefront] Route53 zone for $StorefrontApex"
    $siteZoneId = Get-ZoneId $StorefrontApex
    Ok $siteZoneId

    Step "[Storefront] ALB '$AlbName' and its HTTPS listener"
    $alb = (Invoke-Aws @("elbv2", "describe-load-balancers", "--region", $Region, "--names", $AlbName)).LoadBalancers[0]
    $albDns = $alb.DNSName
    $listener = (Invoke-Aws @("elbv2", "describe-listeners", "--region", $Region, "--load-balancer-arn", $alb.LoadBalancerArn)).Listeners |
        Where-Object { $_.Port -eq 443 } | Select-Object -First 1
    if (-not $listener) { throw "ALB $AlbName has no :443 listener" }
    Ok "$albDns"

    # CloudFront connects to the ALB over HTTPS and sends the shopper's Host
    # (<slug>.onewooz.site), so the ALB must present a *.onewooz.site cert.
    Step "[Storefront] ALB certificate for *.$StorefrontApex (ap-south-1)"
    $regional = Invoke-Aws @("acm", "list-certificates", "--region", $Region, "--certificate-statuses", "ISSUED")
    $siteCert = $regional.CertificateSummaryList |
        Where-Object { $_.DomainName -eq $StorefrontApex -or $_.DomainName -eq "*.$StorefrontApex" } |
        Select-Object -First 1
    if (-not $siteCert) {
        throw ("No ISSUED certificate for $StorefrontApex / *.$StorefrontApex in $Region. " +
               "The ALB must serve the storefront hosts before CloudFront can front them. " +
               "Validate it in ACM ($Region), then re-run with -SkipCdn.")
    }
    $attached = (Invoke-Aws @("elbv2", "describe-listener-certificates", "--region", $Region, "--listener-arn", $listener.ListenerArn)).Certificates |
        Where-Object { $_.CertificateArn -eq $siteCert.CertificateArn }
    if (-not $attached) {
        Invoke-Aws @("elbv2", "add-listener-certificates", "--region", $Region, "--listener-arn", $listener.ListenerArn,
                     "--certificates", "CertificateArn=$($siteCert.CertificateArn)") | Out-Null
        Ok "attached $($siteCert.CertificateArn) to the :443 listener"
    }
    else { Ok "already on the :443 listener" }

    Step "[Storefront] ACM certificate (us-east-1) for $StorefrontApex + *.$StorefrontApex"
    $sfCertArn = Get-CloudFrontCertificate -Primary $StorefrontApex -Alternates @("*.$StorefrontApex") -ZoneId $siteZoneId

    Step "[Storefront] Distribution for *.$StorefrontApex"
    $sfDist = Find-DistributionByAlias "*.$StorefrontApex"
    if ($sfDist) {
        $sfId = $sfDist.Id; $sfDomain = $sfDist.DomainName
        Ok "reusing $sfId ($sfDomain)"
    }
    else {
        # Error pages are never cached: a 403/404 can be about one visitor
        # (their order, their account), and the cache key carries no cookies.
        $errorCodes = @(400, 403, 404, 405, 414, 416, 500, 501, 502, 503, 504)
        $sfConfig = @{
            CallerReference = "onewooz-storefront-" + (Get-Date -Format "yyyyMMddHHmmss")
            Comment = "OneWooz storefronts: *.$StorefrontApex -> ALB"
            Enabled = $true
            Aliases = @{ Quantity = 2; Items = @($StorefrontApex, "*.$StorefrontApex") }
            PriceClass = "PriceClass_200"
            HttpVersion = "http2and3"
            IsIPV6Enabled = $true
            DefaultRootObject = ""
            Origins = @{
                Quantity = 1
                Items = @(
                    @{
                        Id = "onewooz-alb"
                        DomainName = $albDns
                        OriginPath = ""
                        ConnectionAttempts = 3
                        ConnectionTimeout = 10
                        CustomOriginConfig = @{
                            HTTPPort = 80
                            HTTPSPort = 443
                            OriginProtocolPolicy = "https-only"
                            OriginSslProtocols = @{ Quantity = 1; Items = @("TLSv1.2") }
                            OriginReadTimeout = 60       # matches Gunicorn's --timeout
                            OriginKeepaliveTimeout = 60
                        }
                    }
                )
            }
            DefaultCacheBehavior = @{
                TargetOriginId = "onewooz-alb"
                ViewerProtocolPolicy = "redirect-to-https"
                AllowedMethods = @{
                    Quantity = 7; Items = @("GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE")
                    CachedMethods = @{ Quantity = 2; Items = @("GET", "HEAD") }
                }
                Compress = $true
                # Cache only what Django marks shareable; key = Host + query string.
                CachePolicyId = $POLICY_USE_ORIGIN_HEADERS_QS
                # Forward everything (Host, cookies, query) + CloudFront-Viewer-*
                # geo headers, which storefront analytics reads.
                OriginRequestPolicyId = $ORP_ALL_VIEWER_AND_CF
            }
            CustomErrorResponses = @{
                Quantity = $errorCodes.Count
                Items = @($errorCodes | ForEach-Object { @{ ErrorCode = $_; ErrorCachingMinTTL = 0 } })
            }
            ViewerCertificate = @{
                ACMCertificateArn = $sfCertArn
                SSLSupportMethod = "sni-only"
                MinimumProtocolVersion = "TLSv1.2_2021"
            }
        }
        $file = Write-JsonFile "storefront-distribution.json" $sfConfig
        $created = (Invoke-Aws @("cloudfront", "create-distribution", "--distribution-config", $file)).Distribution
        $sfId = $created.Id; $sfDomain = $created.DomainName
        Ok "created $sfId ($sfDomain)"
    }

    # DNS moves only after the distribution is deployed, so no shopper is ever
    # sent to an edge that is not serving yet.
    Step "[Storefront] Waiting for the distribution to deploy (5-15 min)..."
    & aws cloudfront wait distribution-deployed --id $sfId
    if ($LASTEXITCODE -ne 0) { throw "distribution $sfId not deployed yet -- re-run with -SkipCdn once it shows Deployed" }
    Ok "deployed"

    Step "[Storefront] DNS $StorefrontApex + *.$StorefrontApex -> CloudFront"
    Set-AliasRecords -ZoneId $siteZoneId -Names @($StorefrontApex, "*.$StorefrontApex") -CloudFrontDomain $sfDomain
}

Write-Host ""
Write-Host "Done. Next: deploy the app so URLs switch to https://$CdnHost :" -ForegroundColor Green
Write-Host "    powershell -ExecutionPolicy Bypass -File deploy\aws\ha\deploy.ps1" -ForegroundColor Green
