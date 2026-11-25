# OneWooz: remove the old db.t4g.micro database left after the 2026-11-21 upgrade.
#
# onewoozdb2-old1 was stopped (not deleted) as a way back after the blue/green
# switch to db.t4g.small. AWS automatically STARTS a stopped database after 7
# days and bills it again, so run this before 2026-11-28.
#
# Data safety: the snapshots onewoozdb2-old1-at-stop (taken when it stopped)
# and onewoozdb2-pre-optimisation-20261121 are kept, and the live database has
# 7 days of point-in-time backups, so nothing is lost by deleting the instance.
#
# Usage:  .\12-delete-old-db.ps1          # shows what will happen
#         .\12-delete-old-db.ps1 -Apply   # deletes the instance

param([switch]$Apply, [string]$Old = "onewoozdb2-old1")

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""
$R = "ap-south-1"

$state = (aws rds describe-db-instances --region $R --query "DBInstances[?DBInstanceIdentifier=='$Old'].[DBInstanceStatus,DBInstanceClass,DeletionProtection] | [0]" --output text)
if (-not $state -or $state.Trim() -eq "None") { Write-Host "$Old does not exist (already deleted)."; exit 0 }
Write-Host "$Old : $state"
$snaps = aws rds describe-db-snapshots --region $R --query "DBSnapshots[?starts_with(DBSnapshotIdentifier,'onewoozdb2')].[DBSnapshotIdentifier,Status]" --output text
Write-Host "Snapshots kept:`n$snaps"
$live = (aws rds describe-db-instances --region $R --db-instance-identifier onewoozdb2 --query "DBInstances[0].[DBInstanceClass,DBInstanceStatus,BackupRetentionPeriod]" --output text).Trim()
Write-Host "Live database onewoozdb2: $live"
if (-not $Apply) { Write-Host "`nRe-run with -Apply to delete $Old."; exit 0 }

aws rds modify-db-instance --region $R --db-instance-identifier $Old --no-deletion-protection --apply-immediately | Out-Null
if ($LASTEXITCODE -ne 0) { throw "could not turn off deletion protection on $Old" }
Start-Sleep -Seconds 20
aws rds delete-db-instance --region $R --db-instance-identifier $Old --skip-final-snapshot --delete-automated-backups | Out-Null
if ($LASTEXITCODE -ne 0) { throw "delete-db-instance failed" }
Write-Host "Deleting $Old (takes a few minutes). The snapshots above remain."
