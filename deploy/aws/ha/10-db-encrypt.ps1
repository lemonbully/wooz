# OneWooz: encrypt the production database at rest (planned maintenance).
#
# RDS cannot switch encryption on in place, and blue/green deployments cannot
# change it either. The only route is: stop writes, snapshot, copy the snapshot
# with encryption, restore it under the SAME name (so the app's database host
# never changes), restart the app.
#
# DOWNTIME: the whole site is offline for roughly 20 to 30 minutes (snapshot +
# encrypted copy + restore of a ~1 GB database). Run it in the quietest hour
# (02:00 to 04:00 IST) and tell store owners in advance.
#
# Usage:
#   .\10-db-encrypt.ps1            # checks only, changes nothing
#   .\10-db-encrypt.ps1 -Apply     # runs the migration
#
# Safety:
#   * Nothing is deleted. The unencrypted database is renamed
#     onewoozdb2-unencrypted and kept (stopped) as an instant way back.
#   * If any step fails before the new database is live, the script renames the
#     old one back and restarts the app.
#   * Autoscaling is paused while the app is stopped and restored afterwards.

param([switch]$Apply)

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$Cluster = "onewooz-prod"
$Db = "onewoozdb2"
$OldName = "onewoozdb2-unencrypted"
$Stamp = Get-Date -Format "yyyyMMdd-HHmm"
$Snap = "$Db-pre-encrypt-$Stamp"
$EncSnap = "$Db-encrypted-$Stamp"
$AppServices = @("web", "worker", "beat", "marketplace", "worker-marketplace", "worker-media")

function Invoke-Aws { param([string[]]$AwsArgs)
  $out = & aws @AwsArgs
  if ($LASTEXITCODE -ne 0) { throw "aws $($AwsArgs[0..1] -join ' ') failed" }
  return $out
}

# ---- checks (always) ----------------------------------------------------------
$info = (Invoke-Aws @("rds", "describe-db-instances", "--region", $R, "--db-instance-identifier", $Db, "--output", "json")) -join "`n" | ConvertFrom-Json
$i = $info.DBInstances[0]
if ($i.StorageEncrypted) { Write-Host "$Db is already encrypted. Nothing to do."; exit 0 }
if ($i.DBInstanceStatus -ne "available") { throw "$Db is $($i.DBInstanceStatus); wait until it is available" }
$sgIds = @($i.VpcSecurityGroups | ForEach-Object { $_.VpcSecurityGroupId })
$plan = [ordered]@{
  Class = $i.DBInstanceClass; SubnetGroup = $i.DBSubnetGroup.DBSubnetGroupName; SecurityGroups = ($sgIds -join ",")
  ParamGroup = $i.DBParameterGroups[0].DBParameterGroupName; Storage = "$($i.AllocatedStorage) GB $($i.StorageType)"
  MaxStorage = $i.MaxAllocatedStorage; BackupDays = $i.BackupRetentionPeriod
}
Write-Host "Will recreate $Db encrypted with:"; $plan.GetEnumerator() | ForEach-Object { Write-Host ("  {0,-15} {1}" -f $_.Key, $_.Value) }

$desired = @{}
foreach ($s in $AppServices) {
  $d = (aws ecs describe-services --region $R --cluster $Cluster --services $s --query "services[0].desiredCount" --output text).Trim()
  if ($d -and $d -ne "None") { $desired[$s] = [int]$d }
}
Write-Host "App services to stop and restore: $(($desired.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', ')"
if (-not $Apply) { Write-Host "`nChecks passed. Re-run with -Apply in a maintenance window."; exit 0 }

# ---- helpers ------------------------------------------------------------------
function Set-Scaling([bool]$suspend) {
  foreach ($s in $desired.Keys) {
    $state = "DynamicScalingInSuspended=$($suspend.ToString().ToLower()),DynamicScalingOutSuspended=$($suspend.ToString().ToLower()),ScheduledScalingSuspended=$($suspend.ToString().ToLower())"
    aws application-autoscaling register-scalable-target --region $R --service-namespace ecs `
      --resource-id "service/$Cluster/$s" --scalable-dimension ecs:service:DesiredCount --suspended-state $state 2>$null | Out-Null
  }
}
function Stop-App {
  Set-Scaling $true
  foreach ($s in $desired.Keys) { Invoke-Aws @("ecs", "update-service", "--region", $R, "--cluster", $Cluster, "--service", $s, "--desired-count", "0") | Out-Null }
  Invoke-Aws (@("ecs", "wait", "services-stable", "--region", $R, "--cluster", $Cluster, "--services") + @($desired.Keys)) | Out-Null
  Write-Host "App stopped (no more writes)."
}
function Start-App {
  foreach ($s in $desired.Keys) { aws ecs update-service --region $R --cluster $Cluster --service $s --desired-count $desired[$s] | Out-Null }
  Set-Scaling $false
  Write-Host "App restarting: $(($desired.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', ')"
}

$renamed = $false
try {
  Stop-App

  Write-Host "Snapshot $Snap"
  Invoke-Aws @("rds", "create-db-snapshot", "--region", $R, "--db-instance-identifier", $Db, "--db-snapshot-identifier", $Snap) | Out-Null
  Invoke-Aws @("rds", "wait", "db-snapshot-available", "--region", $R, "--db-snapshot-identifier", $Snap) | Out-Null

  Write-Host "Encrypted copy $EncSnap"
  Invoke-Aws @("rds", "copy-db-snapshot", "--region", $R, "--source-db-snapshot-identifier", $Snap, "--target-db-snapshot-identifier", $EncSnap, "--kms-key-id", "alias/aws/rds", "--copy-tags") | Out-Null
  Invoke-Aws @("rds", "wait", "db-snapshot-available", "--region", $R, "--db-snapshot-identifier", $EncSnap) | Out-Null

  Write-Host "Renaming the unencrypted database to $OldName"
  Invoke-Aws @("rds", "modify-db-instance", "--region", $R, "--db-instance-identifier", $Db, "--new-db-instance-identifier", $OldName, "--apply-immediately") | Out-Null
  $renamed = $true
  Start-Sleep -Seconds 60
  Invoke-Aws @("rds", "wait", "db-instance-available", "--region", $R, "--db-instance-identifier", $OldName) | Out-Null

  Write-Host "Restoring encrypted $Db (10 to 15 minutes)"
  $restore = @("rds", "restore-db-instance-from-db-snapshot", "--region", $R, "--db-instance-identifier", $Db,
    "--db-snapshot-identifier", $EncSnap, "--db-instance-class", $plan.Class, "--db-subnet-group-name", $plan.SubnetGroup,
    "--vpc-security-group-ids") + $sgIds + @("--db-parameter-group-name", $plan.ParamGroup, "--no-publicly-accessible",
    "--deletion-protection", "--copy-tags-to-snapshot", "--storage-type", "gp3")
  Invoke-Aws $restore | Out-Null
  Invoke-Aws @("rds", "wait", "db-instance-available", "--region", $R, "--db-instance-identifier", $Db) | Out-Null

  $maxStorage = if ($plan.MaxStorage) { $plan.MaxStorage } else { 100 }
  Invoke-Aws @("rds", "modify-db-instance", "--region", $R, "--db-instance-identifier", $Db, "--backup-retention-period", "$($plan.BackupDays)",
    "--preferred-backup-window", "20:30-21:00", "--preferred-maintenance-window", "sun:21:30-sun:22:00",
    "--max-allocated-storage", "$maxStorage", "--apply-immediately") | Out-Null
  Start-Sleep -Seconds 30
  Invoke-Aws @("rds", "wait", "db-instance-available", "--region", $R, "--db-instance-identifier", $Db) | Out-Null
  $enc = (aws rds describe-db-instances --region $R --db-instance-identifier $Db --query "DBInstances[0].StorageEncrypted" --output text).Trim()
  if ($enc -ne "True") { throw "restored database is not encrypted" }
  Write-Host "Encrypted $Db is live on the same endpoint."
} catch {
  Write-Warning "Migration failed: $_"
  if ($renamed) {
    $exists = (aws rds describe-db-instances --region $R --query "DBInstances[?DBInstanceIdentifier=='$Db'].DBInstanceIdentifier | [0]" --output text).Trim()
    if ($exists -and $exists -ne "None") {
      Write-Warning "A new $Db exists but is not confirmed. NOT renaming back automatically; check it in the console before restarting."
      throw
    }
    Write-Warning "Renaming $OldName back to $Db"
    aws rds modify-db-instance --region $R --db-instance-identifier $OldName --new-db-instance-identifier $Db --apply-immediately | Out-Null
    Start-Sleep -Seconds 60
    aws rds wait db-instance-available --region $R --db-instance-identifier $Db
  }
  Start-App
  throw
}

Start-App
Invoke-Aws (@("ecs", "wait", "services-stable", "--region", $R, "--cluster", $Cluster, "--services") + @($desired.Keys)) | Out-Null
foreach ($u in @("https://onewooz.com/", "https://app.onewooz.com/sign-in/", "https://curarich.in/")) {
  $code = & curl.exe -s -o NUL -w "%{http_code}" --max-time 20 $u
  Write-Host ("  {0,-36} {1}" -f $u, $code)
}
aws rds stop-db-instance --region $R --db-instance-identifier $OldName | Out-Null
Write-Host "`nDone. $OldName is stopped (kept as a way back). Delete it after a week, before AWS auto-starts it on day 7."
