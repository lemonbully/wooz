# OneWooz optimisation, Phase 0 (night part): turn on database backups.
#
# What it does
#   1. Takes a manual snapshot of onewoozdb2 and waits until it is complete.
#   2. Turns on automated backups: 7 days of point-in-time restore.
#      Backup window     20:30-21:00 UTC  (02:00-02:30 IST)
#      Maintenance window Sun 21:30-22:00 UTC (Mon 03:00-03:30 IST)
#
# Why at night
#   The database is a single instance. Taking a snapshot can pause disk I/O for
#   a few seconds, and switching backups on from 0 days can cause a short
#   interruption. Run it at the quietest hour (02:00 to 04:00 IST).
#
# Deletion protection was already switched on (no restart needed).
# Safe to re-run: it skips the snapshot if one was made today.

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"
$Db = "onewoozdb2"
$Snap = "$Db-pre-optimisation-" + (Get-Date -Format "yyyyMMdd")

function Wait-DbAvailable {
  Write-Host "Waiting for $Db to be available..."
  aws rds wait db-instance-available --region $R --db-instance-identifier $Db
  if ($LASTEXITCODE -ne 0) { throw "wait db-instance-available failed" }
}

Wait-DbAvailable

# Filter by name instead of asking for it directly: a direct lookup of a missing
# snapshot writes to stderr, which Windows PowerShell 5.1 turns into a fatal error.
$exists = (aws rds describe-db-snapshots --region $R --db-instance-identifier $Db --snapshot-type manual `
  --query "DBSnapshots[?DBSnapshotIdentifier=='$Snap'].Status | [0]" --output text).Trim()
if (-not $exists -or $exists -eq "None") {
  Write-Host "Creating snapshot $Snap"
  aws rds create-db-snapshot --region $R --db-instance-identifier $Db `
    --db-snapshot-identifier $Snap --tags Key=Purpose,Value=pre-optimisation | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "create-db-snapshot failed" }
} else {
  Write-Host "Snapshot $Snap already exists ($exists)"
}
aws rds wait db-snapshot-available --region $R --db-snapshot-identifier $Snap
if ($LASTEXITCODE -ne 0) { throw "snapshot did not complete" }
Write-Host "Snapshot ready: $Snap"

Wait-DbAvailable
Write-Host "Turning on 7-day automated backups"
aws rds modify-db-instance --region $R --db-instance-identifier $Db `
  --backup-retention-period 7 `
  --preferred-backup-window 20:30-21:00 `
  --preferred-maintenance-window sun:21:30-sun:22:00 `
  --deletion-protection `
  --apply-immediately | Out-Null
if ($LASTEXITCODE -ne 0) { throw "modify-db-instance failed" }

Start-Sleep -Seconds 30
Wait-DbAvailable
aws rds describe-db-instances --region $R --db-instance-identifier $Db `
  --query "DBInstances[0].{status:DBInstanceStatus,backupDays:BackupRetentionPeriod,backupWindow:PreferredBackupWindow,deletionProtection:DeletionProtection,latestRestorable:LatestRestorableTime}" `
  --output table
Write-Host "Done. Check https://app.onewooz.com and a storefront load normally."
