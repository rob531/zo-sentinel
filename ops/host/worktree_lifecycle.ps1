<#
.SYNOPSIS
  The ONE healed implementation of the disposable-worktree lifecycle.

.DESCRIPTION
  `git worktree remove --force` can prune the metadata and still leave the
  directory on disk. On 2026-07-27 it left 3,466 files behind, `git worktree
  list` showed nothing, and the NEXT run died on "fatal: '<path>' already
  exists" -- a state neither `remove` nor `prune` will heal. So: HEAL, RETRY,
  VERIFY, and only then speak.

  WHY THIS FILE EXISTS (FU-157, 2026-07-28). This helper was copied into
  ops/host/deploy_prod.ps1 and ops/host/verify_candidate.ps1, and the copies
  DIVERGED. verify_candidate.ps1 learned that an EMPTY leftover directory is
  not an orphan -- measured: `git worktree add` at an existing empty directory
  succeeds, rc=0, full checkout. deploy_prod.ps1 never learned it. Because
  deploy_prod.ps1 calls this with -MustSucceed $true BEFORE creating its
  worktree, an empty leftover would abort THE ONE PATH THE CHAIRMAN FIRES with
  "a process is holding a file open; find it before firing" -- sending the
  reader hunting a file handle that does not exist, over a condition measured
  to block nothing.

  That is the whole failure mode of a duplicated safeguard: the fix lands in
  the copy that observes, not the copy that acts. One definition, two callers.

  WHAT LEGITIMATELY DIFFERS BY CALL SITE is -MustSucceed and -FatalMessage,
  which is why they are PARAMETERS rather than a forked copy:
    * BEFORE the worktree is created  -> fatal. A stale path means
      `worktree add` cannot pin the sha, so proceeding ships an unknown tree.
    * AFTER a deploy has already run  -> LOUD WARNING, never fatal. The deploy
      and its acceptance gate are the verdict; failing the script over
      leftover files would report a successful ship as a failure.

.NOTES
  Dot-source it, do not copy it:
      . (Join-Path $PSScriptRoot "worktree_lifecycle.ps1")
  A MISSING helper is FATAL at the call site, never a silent skip -- an absent
  guard that reads as a passing one is how a gate that SKIPS becomes a gate
  that PASSES.
#>

# Native-command stderr becomes a TERMINATING error under
# $ErrorActionPreference = "Stop". `git worktree remove` on an absent path is
# expected and harmless, so best-effort git calls go through here: idempotency
# requires the cleanup path to survive a state that was never created.
function Git-BestEffort {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & git @GitArgs 2>&1 | Out-Null } catch { } finally { $ErrorActionPreference = $prev }
}

function Reset-DisposableWorktree {
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [Parameter(Mandatory = $true)][string]$Path,
        [bool]$MustSucceed = $false,
        [int]$Attempts = 5,
        [string]$LogPrefix = "worktree",
        [string]$FatalMessage = ""
    )

    function Note([string]$m) { Write-Host ("[" + $LogPrefix + "] " + $m) }

    # Retry before failing: on Windows a file handle can outlive the process
    # that opened it by a moment, so "cannot delete" is a TIMING fact before it
    # is a FAULT. A closing handle clears in seconds; a wedged process does not.
    # Bounded backoff tells the two apart instead of guessing. Dying on the
    # first attempt turns a clean teardown into a spurious page; never retrying
    # turns a wedge into a silent orphan.
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
            if ($i -gt 1) { Note "worktree path cleared on attempt $i" }
            Note "worktree path VERIFIED gone: $Path"
            return $true
        }

        $left = @(Get-ChildItem $Path -Recurse -Force -ErrorAction SilentlyContinue).Count

        # An EMPTY leftover directory is NOT an orphan, and dying on it turns
        # the guard against orphaned worktrees into the thing that wedges the
        # run. MEASURED 2026-07-28, twice: `git worktree add --detach` at an
        # existing EMPTY directory succeeds (rc=0, full 3,418-file checkout);
        # at a NON-EMPTY one it fails rc=128 "fatal: already exists". So the
        # empty case blocks nothing downstream and the non-empty case blocks
        # everything -- they are different faults and must not share a verdict.
        #
        # Zero entries also means no file handle is closing; what is held is
        # the DIRECTORY ITSELF, almost always because some process has it as
        # its current working directory. Waiting cannot clear that, so retrying
        # four more times and then failing spends 15s to produce a false alarm.
        # Say what is actually true and carry on.
        if ($left -eq 0) {
            Note "worktree path EXISTS but is EMPTY: $Path"
            Note "  an empty dir does not block worktree creation (measured) -- treating as cleared"
            Note "  note: the directory itself is held, not a file -- usually a shell whose CWD is that path"
            return $true
        }

        if ($i -lt $Attempts) {
            $wait = [Math]::Min(8, [Math]::Pow(2, $i - 1))
            Note "attempt $i/$Attempts left $left file(s) at $Path -- retrying in ${wait}s (handle likely still closing)"
            Start-Sleep -Seconds $wait
        } else {
            Note "attempt $i/$Attempts left $left file(s) at $Path"
        }
    }

    if ($MustSucceed) {
        $msg = if ($FatalMessage) { $FatalMessage } else {
            "could not clear $Path after $Attempts attempts -- a process is holding a file open; find it before continuing."
        }
        Write-Host ("[" + $LogPrefix + "] FATAL: " + $msg) -ForegroundColor Red
        exit 1
    }
    Note "WARNING: $Path SURVIVED teardown after $Attempts attempts -- the verdict above still stands, but the next run will inherit this orphan. Clear it by hand: Remove-Item -LiteralPath $Path -Recurse -Force"
    return $false
}
