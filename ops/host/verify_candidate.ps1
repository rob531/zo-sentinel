<#
.SYNOPSIS
  Gate a prod deploy candidate on a clean, disposable worktree -- and keep the
  evidence. $0, read-only, never deploys.

.DESCRIPTION
  Sibling of ops/host/deploy_prod.ps1 (#2063). That script encodes the FIRE path
  so the build args cannot be forgotten; this one encodes the STAGE path that
  runs before it, which prod-drift-sentinel had been hand-rolling in prose every
  3h. Three things went wrong doing it by hand:

    1. ORPHANED WORKTREE. `git worktree remove --force` can leave the directory
       on disk on Windows while the metadata gets pruned. The next run then dies
       on `fatal: 'D:/zo/_prod_dryrun' already exists` with no registered
       worktree to remove -- a state neither `remove` nor `prune` will heal.
       Observed 2026-07-27T16:50Z; the 14:00Z run had recorded
       "worktrees_cleaned": true while 3,466 files sat in that path. A cleanup
       step that is not VERIFIED is a claim, not a result. So: heal any leftover
       directory BEFORE add, and assert the path is gone AFTER remove -- with
       bounded retry, because a handle that is still closing is not a wedge.

    2. THE EVIDENCE WAS DELETED. tools/verify_deploy_candidate.py writes
       artifacts/deploy_candidate_verdict.json INSIDE the worktree -- which the
       very next runbook step removes. The proof behind every stage to date was
       destroyed seconds after being produced. So: rescue the verdict to a
       durable -EvidenceDir before teardown.

    3. A PASS THAT DESCRIBED THE WRONG TREE. A run once accepted a PASS whose
       own output warned the working tree was dirty before the gates ran (stale
       artifacts/ci_smoke_junit.xml from a prior run). The verifier printed the
       truth inside a green result and the runner read past it. So: -Strict
       (default) FAILS on a dirty pre-gate snapshot instead of reporting PASS.

  SAFETY: this script contains no `flyctl deploy`, no `alembic upgrade`, and no
  prod write of any kind -- tests/test_verify_candidate_script.py enforces that.
  Phase-1 prod-drift-sentinel may run it unattended.

.PARAMETER Sha
  Full 40-char commit SHA to gate. Short shas make the gated identity ambiguous.

.PARAMETER EvidenceDir
  Where the verdict JSON is rescued to before the worktree is destroyed.

.PARAMETER Strict
  Fail if the worktree is dirty before the gates run. On by default; -Strict:$false
  downgrades it to a loud warning.

.EXAMPLE
  .\ops\host\verify_candidate.ps1 -Sha 8879ee2676c255797243ec20455105f6ee32123e
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Sha,
    [string]$Repo = "D:\zo\zo-sentinel\zo-sentinel",
    [string]$WorktreePath = "D:\zo\_prod_dryrun",
    [string]$EvidenceDir = "D:\zo\Zocomputer Agents\_deploy_evidence",
    [bool]$Strict = $true
)

$ErrorActionPreference = "Stop"

function Say([string]$m) { Write-Host ("[verify_candidate] " + $m) }
function Die([string]$m) { Write-Host ("[verify_candidate] FATAL: " + $m) -ForegroundColor Red; exit 1 }

# Native git stderr is a TERMINATING error under $ErrorActionPreference = "Stop".
# `worktree remove` on an absent path is expected and harmless: idempotency means
# the cleanup path must survive a state that was never created.
function Git-BestEffort {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & git @GitArgs 2>&1 | Out-Null } catch { } finally { $ErrorActionPreference = $prev }
}

# The whole point of this script. `remove` alone is not enough (see .DESCRIPTION
# note 1) -- prune the metadata, delete any surviving directory, then PROVE it.
function Reset-DisposableWorktree {
    param(
        [string]$RepoPath,
        [string]$Path,
        [bool]$MustSucceed = $false,
        [int]$Attempts = 5
    )
    # RETRY, then verify, then fail. The first cut of this helper died on the
    # first failed removal and immediately produced a false alarm: the gates had
    # just exited and something (an AV scanner, or a reaped child of the smoke
    # ladder's mock write_service) still held 6 of 3,298 files. A retry three
    # seconds later cleared it on the first attempt. On Windows a file handle can
    # outlive the process that opened it by a moment, so "cannot delete" is a
    # TIMING fact before it is a FAULT -- but only briefly. Bounded backoff tells
    # the two apart instead of guessing: a closing handle clears in seconds, a
    # wedged process does not. Dying on the first attempt turns a clean teardown
    # into a spurious page; never retrying turns a wedge into a silent orphan.
    for ($i = 1; $i -le $Attempts; $i++) {
        Push-Location $RepoPath
        try {
            Git-BestEffort worktree remove --force $Path
            Git-BestEffort worktree prune
            if (Test-Path $Path) {
                Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
            }
            Git-BestEffort worktree prune
        } finally { Pop-Location }

        if (-not (Test-Path $Path)) {
            if ($i -gt 1) { Say "worktree path cleared on attempt $i" }
            Say "worktree path VERIFIED gone: $Path"
            return $true
        }

        $left = @(Get-ChildItem $Path -Recurse -Force -ErrorAction SilentlyContinue).Count
        if ($i -lt $Attempts) {
            $wait = [Math]::Min(8, [Math]::Pow(2, $i - 1))
            Say "attempt $i/$Attempts left $left file(s) at $Path -- retrying in ${wait}s (handle likely still closing)"
            Start-Sleep -Seconds $wait
        } else {
            Say "attempt $i/$Attempts left $left file(s) at $Path"
        }
    }

    if ($MustSucceed) {
        Die "could not clear $Path after $Attempts attempts -- refusing to claim a clean teardown. A process is holding a file open; find it before the next run inherits this."
    }
    Say "WARNING: $Path still present after $Attempts attempts"
    return $false
}

if ($Sha -notmatch '^[0-9a-f]{40}$') {
    Die "-Sha must be a full 40-char commit sha (got '$Sha')."
}
if (-not (Test-Path $Repo)) { Die "repo not found: $Repo" }

# ---------------------------------------------------------------- prepare
Reset-DisposableWorktree -RepoPath $Repo -Path $WorktreePath -MustSucceed $true | Out-Null

Push-Location $Repo
try {
    Git-BestEffort fetch origin main --quiet
    Say "creating disposable worktree at $WorktreePath pinned to $Sha"
    & git worktree add --detach $WorktreePath $Sha
    if ($LASTEXITCODE -ne 0) { Die "git worktree add failed (rc=$LASTEXITCODE)" }
} finally { Pop-Location }

$verdictPath = $null
$exitCode = 1

try {
    Push-Location $WorktreePath

    $head = (git rev-parse HEAD).Trim()
    if ($head -ne $Sha) { Die "worktree HEAD $head != requested $Sha" }
    $tree = (git rev-parse 'HEAD^{tree}').Trim()

    # SNAPSHOT BEFORE THE GATES RUN. The gates themselves write tracked artifacts,
    # so measuring dirtiness afterwards measures the gates, not the candidate.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $dirty = @(& git status --porcelain) | Where-Object { $_ }
    $ErrorActionPreference = $prev

    Say "head=$head tree=$tree (the tree that SHIPS)"
    if ($dirty.Count -gt 0) {
        Say "DIRTY BEFORE GATES ($($dirty.Count) path(s)): $($dirty -join '; ')"
        if ($Strict) {
            Die "refusing to gate a dirty tree -- any verdict would describe disk, not $Sha. Re-run after a clean checkout, or pass -Strict:`$false to accept a warning."
        }
        Say "WARNING: -Strict off. The verdict below describes DISK, not $Sha."
    } else {
        Say "working tree CLEAN before gates"
    }

    Say "running tools/verify_deploy_candidate.py"
    & python -u tools/verify_deploy_candidate.py
    $exitCode = $LASTEXITCODE
    Say "verifier exit=$exitCode"

    # RESCUE THE EVIDENCE before teardown destroys it.
    $src = Join-Path $WorktreePath "artifacts\deploy_candidate_verdict.json"
    if (Test-Path $src) {
        if (-not (Test-Path $EvidenceDir)) { New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null }
        $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
        $verdictPath = Join-Path $EvidenceDir ("verdict_{0}_{1}.json" -f $Sha.Substring(0, 8), $stamp)
        Copy-Item $src $verdictPath -Force
        Copy-Item $src (Join-Path $EvidenceDir "verdict_latest.json") -Force
        Say "verdict rescued -> $verdictPath"
    } else {
        Say "WARNING: no verdict artifact at $src -- the verifier did not produce one."
    }
}
finally {
    Pop-Location
    # MustSucceed: a teardown that silently leaves an orphan is what broke the
    # next run. Fail loudly here rather than let a sibling inherit the wreckage.
    Reset-DisposableWorktree -RepoPath $Repo -Path $WorktreePath -MustSucceed $true | Out-Null
}

if ($verdictPath) { Say "EVIDENCE: $verdictPath" }
Say ("RESULT: {0}" -f $(if ($exitCode -eq 0) { "PASS" } else { "FAIL (rc=$exitCode)" }))
exit $exitCode
