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

# ---------------------------------------------------------------- worktree lifecycle
# SINGLE SOURCE (FU-157). Reset-DisposableWorktree and Git-BestEffort used to be
# copied into this file AND into ops/host/deploy_prod.ps1. The copies diverged: the observer
# learned that an EMPTY leftover directory is harmless (measured) and the actor
# did not, so the fire path would abort on a condition that blocks nothing.
# One definition, two callers. What legitimately differs by call site
# (-MustSucceed, -FatalMessage) is a parameter, not a fork.
$WorktreeLifecycle = Join-Path $PSScriptRoot "worktree_lifecycle.ps1"
if (-not (Test-Path $WorktreeLifecycle)) {
    # An absent guard must never read as a passing one. Name the file and die.
    Die "worktree_lifecycle.ps1 not found beside this script ($WorktreeLifecycle). Refusing to run without the healed worktree teardown -- that helper IS the guard against the 3,466-file orphan."
}
. (Join-Path $PSScriptRoot "worktree_lifecycle.ps1")

if ($Sha -notmatch '^[0-9a-f]{40}$') {
    Die "-Sha must be a full 40-char commit sha (got '$Sha')."
}
if (-not (Test-Path $Repo)) { Die "repo not found: $Repo" }

# ---------------------------------------------------------------- prepare
Reset-DisposableWorktree -RepoPath $Repo -Path $WorktreePath -MustSucceed $true -LogPrefix "verify_candidate" -FatalMessage 'refusing to claim a clean teardown -- a NON-EMPTY leftover survived. A process is holding a file open; find it before the next run inherits this.' | Out-Null

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

        # STAMP THE PRODUCING LANE. $EvidenceDir is SHARED by every lane, and
        # tools/sentinel_run_ledger.py reads it as though prod-drift owned it.
        # A shared basename is a shared counter: on 2026-08-02 a sibling lane's
        # 18:15Z dry-run of THIS script surfaced in prod-drift's ledger as
        # ORPHAN EVIDENCE, an alarm about a lane that had done nothing wrong.
        # The artifact is the only party that knows who wrote it, so it is the
        # party that has to say so. Additive key; no existing field is touched
        # and any reader that ignores it behaves exactly as before.
        $lane = $env:ZO_LANE
        if (-not $lane -and $PSScriptRoot -match '\\_lanes\\([^\\]+)') { $lane = $Matches[1] }
        if (-not $lane) { $lane = "unattributed" }
        $stampSrc = @'
import json, sys
lane, paths = sys.argv[1], sys.argv[2:]
for p in paths:
    try:
        with open(p, encoding="utf-8") as fh:
            blob = json.load(fh)
        if not isinstance(blob, dict):
            continue
        blob["produced_by_lane"] = lane
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, indent=2)
    except Exception as exc:
        print("WARN: could not stamp %s: %s" % (p, exc))
'@
        $stampFile = Join-Path ([IO.Path]::GetTempPath()) ("stamp_lane_{0}.py" -f [Guid]::NewGuid().ToString("N"))
        Set-Content -Path $stampFile -Value $stampSrc -Encoding UTF8
        try {
            python $stampFile $lane $verdictPath (Join-Path $EvidenceDir "verdict_latest.json")
            Say "stamped produced_by_lane=$lane"
        } finally {
            Remove-Item $stampFile -Force -ErrorAction SilentlyContinue
        }
    } else {
        Say "WARNING: no verdict artifact at $src -- the verifier did not produce one."
    }
}
finally {
    Pop-Location
    # MustSucceed: a teardown that silently leaves an orphan is what broke the
    # next run. Fail loudly here rather than let a sibling inherit the wreckage.
    Reset-DisposableWorktree -RepoPath $Repo -Path $WorktreePath -MustSucceed $true -LogPrefix "verify_candidate" -FatalMessage 'refusing to claim a clean teardown -- a NON-EMPTY leftover survived. A process is holding a file open; find it before the next run inherits this.' | Out-Null
}

if ($verdictPath) { Say "EVIDENCE: $verdictPath" }
Say ("RESULT: {0}" -f $(if ($exitCode -eq 0) { "PASS" } else { "FAIL (rc=$exitCode)" }))
exit $exitCode
