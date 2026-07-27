<#
.SYNOPSIS
  Fire a prod (Fly app `mcplookup`) release from a clean, disposable worktree.

.DESCRIPTION
  Encodes the release-manager runbook proven 2026-07-25 (v63 -> v64) so the two
  things that were remembered-and-forgotten become mechanical:

    1. NEVER deploy from the shared checkout D:\zo\zo-sentinel\zo-sentinel. It is
       often on a wrong branch, hundreds of commits behind, dirty, and its local
       `main` ref is locked in one of ~14 worktrees. This script always builds
       from a disposable `git worktree add --detach <SHA>`.

    2. ALWAYS pass --build-arg GIT_SHA / BUILD_TIME. The Dockerfile has declared
       `ARG GIT_SHA=unknown` and exposed it via /version
       (runtime_deploy_info_endpoint) since before v64 -- but no deploy ever
       passed the arg, so prod has served `"git_sha":"unknown"` its entire life.
       That is why prod-drift-sentinel could only record an APPROXIMATE prod
       commit and had to infer drift from the release timestamp. One forgotten
       flag turned a measured fact into a guess. Here it is not optional.

  AUTHORITY: this script is fired by a human (the chairman). prod-drift-sentinel
  is Phase 1 and MUST NOT invoke it without -DryRun -- it stages the command, it
  does not push. Nothing here grants an agent deploy authority.

  IRREVERSIBLE EDGE: fly.toml carries `release_command = "alembic upgrade head"`,
  which runs against the prod moat Postgres on every release. There is no true
  Fly migration rollback. Only fire when the staged migration class is GREEN
  (expand-only or no-op) and a moat backup < 24h exists.

.PARAMETER Sha
  Full 40-char commit SHA to deploy. Must be a CI-green origin/main commit.

.PARAMETER RollbackImage
  Current prod release image, recorded before the deploy as the rollback anchor
  (e.g. registry.fly.io/mcplookup:deployment-01KYDJH...). If omitted the script
  reads it from `flyctl releases` before deploying.

.PARAMETER DryRun
  Do everything except the deploy itself: create the worktree, resolve the build
  args, print the exact command, probe the CURRENT prod surfaces, remove the
  worktree. $0 and safe -- this is how the sentinel verifies the script.

.EXAMPLE
  .\ops\host\deploy_prod.ps1 -Sha 88c1043df61257cad25a522c798d8924aa582d01 -DryRun
  .\ops\host\deploy_prod.ps1 -Sha 88c1043df61257cad25a522c798d8924aa582d01
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Sha,
    [string]$RollbackImage = "",
    [string]$Repo = "D:\zo\zo-sentinel\zo-sentinel",
    [string]$WorktreePath = "D:\zo\_deploy_prod",
    [string]$App = "mcplookup",
    [string]$BaseUrl = "https://mcprisky.io",
    [int]$PollSeconds = 120,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Say([string]$m) { Write-Host ("[deploy_prod] " + $m) }
function Die([string]$m) { Write-Host ("[deploy_prod] FATAL: " + $m) -ForegroundColor Red; exit 1 }

# Native-command stderr becomes a TERMINATING error under $ErrorActionPreference
# = "Stop". `git worktree remove` on an absent path is expected and harmless, so
# best-effort git calls go through here. Idempotency requires the cleanup path to
# survive a state that was never created.
function Git-BestEffort {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & git @GitArgs 2>&1 | Out-Null } catch { } finally { $ErrorActionPreference = $prev }
}

# ---------------------------------------------------------------- teardown
# HEAL, RETRY, VERIFY. `git worktree remove --force` can prune the metadata and
# still leave the directory on disk: on 2026-07-27 it left 3,466 files behind,
# `git worktree list` showed nothing, and the NEXT run died on
# "fatal: '<path>' already exists" -- a state neither `remove` nor `prune` can
# fix. The old teardown here was `Remove-Item ... -ErrorAction SilentlyContinue`
# with no check after it, so that failure mode was SILENT in the one path the
# chairman actually fires.
#
# Retry before failing: on Windows a file handle can outlive the process that
# opened it by a moment, so "cannot delete" is a TIMING fact before it is a
# FAULT. A closing handle clears in seconds; a wedged process does not. Bounded
# backoff tells the two apart instead of guessing.
#
# MustSucceed differs by call site ON PURPOSE, and this is where this wrapper
# diverges from verify_candidate.ps1:
#   * BEFORE the worktree is created -- fatal. A stale path means `worktree add`
#     cannot pin the sha, so deploying anyway would ship an unknown tree.
#   * AFTER a deploy has already run -- LOUD WARNING, never fatal. The deploy and
#     its acceptance gate are the verdict; failing the script over leftover files
#     would report a successful ship as a failure. Loud, not fatal, not silent.
function Reset-DisposableWorktree {
    param(
        [string]$RepoPath,
        [string]$Path,
        [bool]$MustSucceed = $false,
        [int]$Attempts = 5
    )
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
        Die "could not clear $Path after $Attempts attempts -- refusing to deploy from a path that cannot be pinned to $Sha. A process is holding a file open; find it before firing."
    }
    Say "WARNING: $Path SURVIVED teardown after $Attempts attempts -- the deploy verdict above still stands, but the next run will inherit this orphan. Clear it by hand: Remove-Item -LiteralPath $Path -Recurse -Force"
    return $false
}

if ($Sha -notmatch '^[0-9a-f]{40}$') {
    Die "-Sha must be a full 40-char commit sha (got '$Sha'). Short shas make the deployed identity ambiguous."
}

# ---------------------------------------------------------------- token
if (-not $env:FLY_API_TOKEN) {
    Say "fetching Fly token from AgentVault"
    $env:FLY_API_TOKEN = (python D:\agentvault\fetch_secret.py fly)
}
if (-not $env:FLY_API_TOKEN) { Die "no FLY_API_TOKEN (AgentVault: python D:\agentvault\fetch_secret.py fly)" }

# ---------------------------------------------------------------- rollback anchor
if (-not $RollbackImage) {
    Say "reading current prod release as the rollback anchor"
    $rel = (flyctl releases --app $App --json | ConvertFrom-Json)
    if (-not $rel) { Die "could not read flyctl releases --app $App" }
    $RollbackImage = $rel[0].ImageRef
    Say ("current prod: v{0} {1} ({2})" -f $rel[0].Version, $rel[0].ImageRef, $rel[0].CreatedAt)
}
if (-not $RollbackImage) { Die "no rollback anchor resolved -- refusing to deploy without one" }

$rollbackCmd = "flyctl deploy --app $App --image $RollbackImage --yes"
Say "ROLLBACK ANCHOR: $rollbackCmd"

# ---------------------------------------------------------------- clean worktree
Push-Location $Repo
Git-BestEffort fetch origin main --quiet
Pop-Location
# fatal if it will not clear: see Reset-DisposableWorktree
[void](Reset-DisposableWorktree -RepoPath $Repo -Path $WorktreePath -MustSucceed $true)
Push-Location $Repo
Say "creating disposable worktree at $WorktreePath pinned to $Sha"
git worktree add --detach $WorktreePath $Sha
Pop-Location
if (-not (Test-Path (Join-Path $WorktreePath "fly.toml"))) { Die "no fly.toml in the worktree -- wrong sha or bad checkout" }

try {
    Push-Location $WorktreePath

    $head = (git rev-parse HEAD).Trim()
    if ($head -ne $Sha) { Die "worktree HEAD $head != requested $Sha" }
    $tree = (git rev-parse 'HEAD^{tree}').Trim()
    $buildTime = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")

    Say "head=$head tree=$tree build_time=$buildTime"
    Say "release_command = 'alembic upgrade head' WILL run against the prod moat PG."

    $deployCmd = "flyctl deploy --app $App --yes --build-arg GIT_SHA=$Sha --build-arg BUILD_TIME=$buildTime"
    Say "DEPLOY COMMAND: $deployCmd"

    if ($DryRun) {
        Say "--DryRun: not deploying. Probing CURRENT prod surfaces for reference."
        foreach ($p in "/health", "/version", "/spine/health") {
            try {
                $r = Invoke-WebRequest -Uri ($BaseUrl + $p) -UseBasicParsing -TimeoutSec 25
                Say ("  {0} -> {1} {2}" -f $p, $r.StatusCode, $r.Content.Substring(0, [Math]::Min(220, $r.Content.Length)))
            } catch {
                Say ("  {0} -> ERR {1}" -f $p, $_.Exception.Message)
            }
        }
        Say "DryRun complete. Nothing was deployed."
        return
    }

    # ------------------------------------------------------------ fire
    Say "deploying (build + release typically ~5 min)"
    & flyctl deploy --app $App --yes --build-arg "GIT_SHA=$Sha" --build-arg "BUILD_TIME=$buildTime"
    $rc = $LASTEXITCODE
    Say "flyctl deploy exit=$rc"

    # ------------------------------------------------------------ verify
    # Acceptance: /health 200 AND /version reports OUR sha AND /spine/health 200
    # with ok:true and an EMPTY failures[]. /version is the whole point of the
    # build args: it proves the running image is the tree that was gated.
    $deadline = (Get-Date).AddSeconds($PollSeconds)
    $pass = $false
    $lastSpine = ""
    $lastVersion = ""
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        try {
            $h = Invoke-WebRequest -Uri ($BaseUrl + "/health") -UseBasicParsing -TimeoutSec 20
            $v = Invoke-WebRequest -Uri ($BaseUrl + "/version") -UseBasicParsing -TimeoutSec 20
            $s = Invoke-WebRequest -Uri ($BaseUrl + "/spine/health") -UseBasicParsing -TimeoutSec 20
            $vj = $v.Content | ConvertFrom-Json
            $sj = $s.Content | ConvertFrom-Json
            $lastVersion = $v.Content
            $lastSpine = ("ok={0} services={1} failures={2}" -f $sj.ok, $sj.service_count, @($sj.failures).Count)
            Say ("  probe: health={0} version.git_sha={1} spine[{2}]" -f $h.StatusCode, $vj.git_sha, $lastSpine)
            if ($h.StatusCode -eq 200 -and $s.StatusCode -eq 200 -and $vj.git_sha -eq $Sha -and $sj.ok -eq $true -and @($sj.failures).Count -eq 0) {
                $pass = $true
                break
            }
        } catch {
            Say ("  probe error: " + $_.Exception.Message)
        }
    }

    if ($pass) {
        Say "ACCEPTED: /health 200, /version git_sha=$Sha, /spine/health ok:true failures[] empty."
        Say "CAVEAT (FU-114): an empty failures[] means every active service MOUNTED. A service that declares no router mounts clean while serving nothing. Green != serving."
        Say "Record the accepted sha in D:\zo\Zocomputer Agents\prod_deploy_state.json."
    } else {
        Say "NOT ACCEPTED within $PollSeconds s."
        Say "  last /version : $lastVersion"
        Say "  last /spine   : $lastSpine"
        Say "READ BEFORE ROLLING BACK: /spine/health ok:false with a failures[] list is the fail-loud spine WORKING, not an outage -- the app serves 200 and mounted services work. Roll back for 5xx or a wrong git_sha, not for a failures[] list that can be fixed forward."
        Say "ROLLBACK: $rollbackCmd"
    }
}
finally {
    Pop-Location
    Say "removing disposable worktree $WorktreePath"
    # loud, never fatal: the deploy verdict above is the result, not this
    [void](Reset-DisposableWorktree -RepoPath $Repo -Path $WorktreePath -MustSucceed $false)
}
