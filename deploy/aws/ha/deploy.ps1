<#
OneWooz one-command deploy.

Builds the app image, pushes it to ECR, then pins the EXACT pushed digest into
the web / worker / beat task definitions, registers new revisions, and rolls the
services.

Why pin the digest instead of using :latest?
  ECS on EC2 caches floating tags. If the task definition says ":latest", an
  instance that already pulled an older ":latest" will happily reuse the cached
  image and your new build never runs (unless every instance is set to
  ECS_IMAGE_PULL_BEHAVIOR=always). A digest the instance does not have MUST be
  pulled, so pinning is deterministic. The only downside of pinning -- updating
  the digest by hand each build -- is exactly what this script removes.

Prerequisites: Docker Desktop running, AWS credentials configured (the same
ones you use for `aws` on the command line), run from anywhere in the repo.

Examples (from the repo root):
    powershell -ExecutionPolicy Bypass -File deploy\aws\ha\deploy.ps1
    powershell -ExecutionPolicy Bypass -File deploy\aws\ha\deploy.ps1 -SkipBuild
    powershell -ExecutionPolicy Bypass -File deploy\aws\ha\deploy.ps1 -Services web
#>
[CmdletBinding()]
param(
    [string]   $Region   = "ap-south-1",
    [string]   $Cluster  = "onewooz-prod",
    [string]   $Account  = "521831014282",
    [string]   $Repo     = "onewooz-app",
    # worker-media is included by default so the video-compression worker always
    # runs the current image; it sits at 0 tasks until a video is uploaded.
    [string[]] $Services = @("web", "worker", "beat", "worker-media"),
    [switch]   $SkipBuild,   # reuse whatever :latest already is in ECR
    [switch]   $NoWait       # don't block waiting for services to stabilise
)

$ErrorActionPreference = "Stop"

# `powershell -File deploy.ps1 -Services web,worker` passes ONE string
# "web,worker" (not an array), so split on commas whichever way it arrives.
$Services = @($Services | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

# service name -> { family, taskdef json file }  (all three share one image)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path      # deploy\aws\ha
$repoRoot = (Resolve-Path (Join-Path $here "..\..\..")).Path
$ecr = "$Account.dkr.ecr.$Region.amazonaws.com/$Repo"

$map = @{
    web    = @{ family = "onewooz-web";    file = (Join-Path $here "taskdef-web.json") }
    worker = @{ family = "onewooz-worker"; file = (Join-Path $here "taskdef-worker.json") }
    beat   = @{ family = "onewooz-beat";   file = (Join-Path $here "taskdef-beat.json") }
    # OneWooz Apps (marketplace.onewooz.com) and its dedicated Celery worker.
    # Include them with: -Services web,worker,beat,marketplace,worker-marketplace
    marketplace          = @{ family = "onewooz-marketplace";        file = (Join-Path $here "taskdef-marketplace.json") }
    "worker-marketplace" = @{ family = "onewooz-worker-marketplace"; file = (Join-Path $here "taskdef-worker-marketplace.json") }
    # Video compression (ffmpeg) on the "media" queue; scales 0 <-> 2 on queue length.
    "worker-media"       = @{ family = "onewooz-worker-media";       file = (Join-Path $here "taskdef-worker-media.json") }
}

function Invoke-Checked([string]$What, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

Push-Location $repoRoot
try {
    if (-not $SkipBuild) {
        Write-Host "==> Building image (linux/arm64)..." -ForegroundColor Cyan
        Invoke-Checked "docker build" { docker build --platform linux/arm64 -t "${Repo}:latest" -f deploy/Dockerfile . }

        Write-Host "==> Tagging + pushing to ECR..." -ForegroundColor Cyan
        Invoke-Checked "docker tag"  { docker tag "${Repo}:latest" "${ecr}:latest" }
        Invoke-Checked "docker push" { docker push "${ecr}:latest" }
    }
    else {
        Write-Host "==> -SkipBuild: reusing the current :latest in ECR." -ForegroundColor Yellow
    }

    Write-Host "==> Resolving the pushed digest..." -ForegroundColor Cyan
    $digest = (aws ecr describe-images --region $Region --repository-name $Repo `
                   --image-ids imageTag=latest --query "imageDetails[0].imageDigest" --output text).Trim()
    if (-not $digest -or $digest -eq "None") { throw "could not resolve the :latest digest in ECR" }
    $pinned = "${ecr}@${digest}"
    Write-Host "    $pinned" -ForegroundColor Green

    # Pin, register and roll each requested service.
    $imgPattern = [regex]::Escape($ecr) + "(?::latest|@sha256:[a-f0-9]+)"
    $utf8NoBom  = New-Object System.Text.UTF8Encoding($false)

    foreach ($svc in $Services) {
        if (-not $map.ContainsKey($svc)) { Write-Warning "unknown service '$svc' -- skipping"; continue }
        $family = $map[$svc].family
        $file   = $map[$svc].file
        $name   = [IO.Path]::GetFileName($file)

        Write-Host "==> [$svc] pinning digest in $name" -ForegroundColor Cyan
        $text = Get-Content -Raw -Path $file
        $text = [regex]::Replace($text, $imgPattern, $pinned)   # $pinned has no '$' to escape
        [System.IO.File]::WriteAllText($file, $text, $utf8NoBom)

        Write-Host "==> [$svc] registering task definition $family" -ForegroundColor Cyan
        Invoke-Checked "register-task-definition ($family)" {
            aws ecs register-task-definition --region $Region --cli-input-json "file://$file" | Out-Null
        }

        Write-Host "==> [$svc] rolling service" -ForegroundColor Cyan
        Invoke-Checked "update-service ($svc)" {
            aws ecs update-service --region $Region --cluster $Cluster --service $svc `
                --task-definition $family --force-new-deployment | Out-Null
        }
    }

    if (-not $NoWait) {
        Write-Host "==> Waiting for services to stabilise (Ctrl+C is safe -- the roll continues)..." -ForegroundColor Cyan
        aws ecs wait services-stable --region $Region --cluster $Cluster --services $Services
        if ($LASTEXITCODE -ne 0) { throw "services did not stabilise (check each service's Events / Logs tab)" }
        Write-Host "==> Stable." -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Deployed $pinned to: $($Services -join ', ')" -ForegroundColor Green
}
finally {
    Pop-Location
}
