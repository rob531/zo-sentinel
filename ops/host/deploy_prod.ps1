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

    3. The ACCEPTANCE VERDICT IS CODE, NOT PROSE, AND IT LEAVES AN EXIT CODE.
       This script used to poll inline and then *say* whether it liked what it
       saw, always exiting 0 -- so a NOT-ACCEPTED deploy was indistinguishable
       from a clean one to anything reading $LASTEXITCODE. It now delegates to
       tools/accept_gate.py and exits 0 ACCEPT / 1 REJECT / 2 ERROR. Phase 2
       promotion is gated on a count of clean staged->fired deploys; a runbook
       that cannot report its own failure cannot be counted.

  AUTHORITY: READ D:\zo\Zocomputer Agents\authority.json AT RUN TIME. DO NOT TRUST
  THIS COMMENT AS A GRANT OR A DENIAL -- a docstring cannot be revoked, and this one
  was wrong for three days.

    Until 2026-08-02 this block read: "this script is fired by a human (the
    chairman). prod-drift-sentinel is Phase 1 and MUST NOT invoke it without
    -DryRun." That was the 2026-07-25 CofC Phase 1 rule, and the chairman's
    2026-07-29 grant retired it BY NAME AND BY LANE:
      authority.json.supersedes_prose[1] =
        "prod_drift_sentinel CofC Phase 1 'stage, never fire' (2026-07-25) ..."
      authority.json.delegated.prod_deploy_fire =
        { granted: true, mode: FIRE_ON_GREEN, phase: 2 }
    Nobody updated the prose. The lane kept obeying an instruction its principal
    had already withdrawn: 20 stages, 0 fires, prod drift 218 -> 402 -> 423 -> 452
    commits, every stage correctly computed and not one of them actionable. That is
    a correctly raised alarm with no subscriber. The fix is not another gate; it is
    that permission lives in ONE machine-readable file and nowhere else.

  So: an agent MAY fire this script when authority.json grants prod_deploy_fire AND
  all five preconditions hold -- 8/8 gates PASS, fire_gate SAFE rc=0, restore-verified
  backup < 24h, rollback anchor staged AND PROVEN PULLABLE BEFORE the fire, and
  accept_gate rc=0 after. Class B (migrations tree object differs between the running
  sha and the candidate, per `git rev-parse <sha>:migrations`) is ATTENDED-ONLY
  PERMANENTLY regardless of gates, because of the IRREVERSIBLE EDGE below.
  First lane-fired release: v66, sha d5cb1d0f, 2026-08-02, accept_gate ACCEPT rc=0.

  IRREVERSIBLE EDGE: fly.toml carries a `release_command` that runs alembic
  against the prod moat Postgres on every release. This comment deliberately
  does NOT restate the command -- it did until 2026-08-04 and was wrong for the
  v69 fire. The script READS the live value out of the candidate's fly.toml and
  prints it before deploying; trust that line, not this one. There is no true
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

# Default ERROR. A run that falls over before establishing anything must NOT be
# readable as success by whatever reads $LASTEXITCODE -- 0 is a claim, and it has
# to be earned. 0 ACCEPT / 1 REJECT / 2 ERROR, matching tools/accept_gate.py.
$script:Verdict = 2

# The acceptance gate belongs to the RUNBOOK, not to the deployed tree: the sha
# being shipped generally predates it (7fc39201 does). Resolve it next to THIS
# script, never inside $WorktreePath.
$AcceptGate = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "tools\accept_gate.py"

function Say([string]$m) { Write-Host ("[deploy_prod] " + $m) }
function Die([string]$m) { Write-Host ("[deploy_prod] FATAL: " + $m) -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- worktree lifecycle
# SINGLE SOURCE (FU-157). Reset-DisposableWorktree and Git-BestEffort used to be
# copied into this file AND into ops/host/verify_candidate.ps1. The copies diverged: the observer
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
[void](Reset-DisposableWorktree -RepoPath $Repo -Path $WorktreePath -MustSucceed $true -LogPrefix "deploy_prod" -FatalMessage 'refusing to deploy from a path that cannot be pinned to the requested sha. A NON-EMPTY leftover blocks git worktree add (measured: rc=128, already exists), so this is a real wedge, not a closing handle: a process is holding a file open. Find it before firing.')
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
    # READ the release_command; do not restate it. This line hardcoded
    # 'alembic upgrade head' and went stale the moment FU-235 (#2775) changed
    # fly.toml to run alembic under $OWNER_DATABASE_URL. It is the one line an
    # operator reads to learn what is about to touch the moat, and for the
    # duration of the v69 fire it would have named the wrong command. A runbook
    # that restates a value it is standing next to is a second source of truth,
    # and the second one only ever drifts.
    $flyTomlPath = Join-Path $WorktreePath "fly.toml"
    $relLine = Select-String -Path $flyTomlPath -Pattern '^\s*release_command\s*=' |
               Select-Object -First 1
    if (-not $relLine) {
        Die "no release_command in the candidate's fly.toml ($flyTomlPath). Refusing to fire without knowing what runs against the prod moat PG -- an unreadable value must not read as a safe one."
    }
    $relCmd = ($relLine.Line -replace '^\s*release_command\s*=\s*', '').Trim()
    Say "release_command (READ from the candidate's fly.toml, not restated):"
    Say "  $relCmd"
    Say "That WILL run against the prod moat PG."

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
        if (Test-Path $AcceptGate) {
            Say "acceptance gate present: $AcceptGate -- probing CURRENT prod through it (expect REJECT while prod is stale; that is the negative control)"
            & python $AcceptGate --sha $Sha --base-url $BaseUrl --once --rollback-image $RollbackImage
            Say "accept_gate exit=$LASTEXITCODE (1 = REJECT, expected pre-deploy)"
        } else {
            Say "WARNING: acceptance gate NOT FOUND at $AcceptGate -- a real deploy would have no machine verdict."
        }
        Say "DryRun complete. Nothing was deployed."
        $script:Verdict = 0
        exit 0
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
    #
    # This was 25 lines of inline polling that ended in Write-Host. It is now
    # tools/accept_gate.py: the same assertions, but unit-tested (22 tests, each
    # seen RED under mutation) and reusable by prod-drift-sentinel's post-fire
    # verify, which was re-deriving the same rule from prose.
    if (-not (Test-Path $AcceptGate)) {
        # A missing gate is an ERROR, never a pass. The failure mode this whole
        # file exists to prevent is a check that silently does not run.
        Say "FATAL: acceptance gate not found at $AcceptGate. The deploy HAS FIRED; it is simply unverified."
        Say "Verify by hand, then record the outcome:"
        Say "  curl https://mcprisky.io/version   # git_sha must be $Sha"
        Say "  curl https://mcprisky.io/spine/health   # ok:true, failures[] empty"
        Say "ROLLBACK IF WRONG: $rollbackCmd"
        $script:Verdict = 2
    }
    else {
        & python $AcceptGate --sha $Sha --base-url $BaseUrl --timeout-seconds $PollSeconds --rollback-image $RollbackImage
        $script:Verdict = $LASTEXITCODE
        switch ($script:Verdict) {
            0 { Say "ACCEPTED. Record the accepted sha in D:\zo\Zocomputer Agents\prod_deploy_state.json and increment clean_staged_fired_deploys." }
            1 { Say "REJECTED. Read accept_gate's reasons above BEFORE rolling back -- a populated failures[] is fix-forward, a wrong git_sha is not." ; Say "ROLLBACK: $rollbackCmd" }
            default { Say "ERROR -- prod could not be read, so NOTHING was established. This is not a red. Re-probe before acting: python $AcceptGate --sha $Sha --once" }
        }
    }
}
finally {
    Pop-Location
    Say "removing disposable worktree $WorktreePath"
    # loud, never fatal: the deploy verdict above is the result, not this
    [void](Reset-DisposableWorktree -RepoPath $Repo -Path $WorktreePath -MustSucceed $false -LogPrefix "deploy_prod")
}

# The last word is the verdict, not the teardown. Teardown warns loudly and is
# deliberately never fatal (see Reset-DisposableWorktree): failing the script over
# leftover files would report a successful ship as a failure.
exit $script:Verdict
