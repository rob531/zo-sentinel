# queue_census_task.ps1 -- hourly tower runner for tools/queue_census.py
#
# Deliberately READ-ONLY with respect to the repo: it does NOT pull. The census
# queries GitHub live, so a slightly stale checkout only affects the tool's own
# code, whereas an auto-pull inside a scheduled task is how runtime state gets
# reverted underneath a running daemon.
#
# Exit 1 from the census means ALARM, which is a normal outcome, not a task
# failure -- so this wrapper always exits 0 and records the verdict instead.
# Otherwise Task Scheduler's "last result" column becomes a second, worse alarm
# channel that nobody has agreed thresholds for.

param(
  [string]$Repo = "D:\zo\zo-sentinel\zo-sentinel"
)

$ErrorActionPreference = "Continue"
$py  = "C:\Users\robin\AppData\Local\Programs\Python\Python311\python.exe"
$out = Join-Path $Repo "artifacts\queue_census"
New-Item -ItemType Directory -Force $out | Out-Null

$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$log   = Join-Path $out "task.log"

Push-Location $Repo
try {
  $text = & $py "tools\queue_census.py" 2>&1 | Out-String
  $code = $LASTEXITCODE
} catch {
  $text = "census RAISED: $_"
  $code = 2
} finally {
  Pop-Location
}

$verdict = if ($code -eq 0) { "OK" } elseif ($code -eq 1) { "ALARM" } else { "ERROR($code)" }
Add-Content -Path $log -Value "===== $stamp verdict=$verdict"
Add-Content -Path $log -Value $text
Set-Content -Path (Join-Path $out "last_run.txt") -Value "$stamp verdict=$verdict`n$text"

# Keep the log from growing without bound; the JSON snapshots are the real history.
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 2MB)) {
  $tail = Get-Content $log -Tail 2000
  Set-Content -Path $log -Value $tail
}

exit 0
