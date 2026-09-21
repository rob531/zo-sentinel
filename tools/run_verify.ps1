# run_verify.ps1 -- launch verify_candidate.ps1 so it OUTLIVES its caller.
#
# 2026-07-28 (prod-drift-sentinel, FU-138). verify_candidate.ps1 runs ~90s+ (the
# smoke-ladder gate alone is 26-67s). prod-drift-sentinel invokes it in the
# FOREGROUND from an agent/MCP shell whose request timeout is shorter. What then
# happens is precise and repeatable:
#
#   * the script builds D:\zo\_prod_dryrun, runs 8 gates, writes the verdict JSON
#   * the MCP request times out; the parent PowerShell is torn down
#   * the child dies BEFORE its `git worktree remove --force` teardown
#   * D:\zo\_prod_dryrun survives as an orphan
#
# So the orphan worktree that #2173/FU-130 taught verify_candidate to HEAL has a
# cause nobody had named: the foreground launch. #2173 fixed the symptom
# (tolerate an empty orphan dir) and was right to, but the orphan is
# manufactured fresh on every slow run rather than being a one-off residue.
# Observed live 2026-07-28T10:57Z: verdict written at 10:57:29Z, PID 2160 gone,
# 1,621 files stranded, and the directory handle still held afterwards.
#
# Same remedy as the nightly backup (FU-112): launch detached under cmd.exe via a
# per-run .cmd, redirect both streams to dated files, have the CHILD record its
# own exit code. The caller polls FILES instead of holding a pipe, so no request
# timeout can kill a healthy verification -- or, more to the point, kill its
# cleanup.
#
#   powershell -File run_verify.ps1 -Sha <FULL 40-CHAR SHA>       # launch, return
#   powershell -File run_verify.ps1 -Sha <SHA> -Wait              # block until done
#
# -Sha is MANDATORY here for the same reason it is in verify_candidate.ps1
# (FU-130): omitted, that script blocks forever on an invisible stdin prompt and
# writes a 0-byte log. Detached, an invisible prompt is not merely invisible, it
# is unanswerable -- so this launcher validates the SHAPE of the SHA before it
# ever spawns, rather than discovering it in a hung child.
#
# FU-153 (2026-07-28): this file previously existed ONLY at a Cowork-workspace
# path outside the repo, so the next unattended run could not find it and the
# FU-138 fix was inert -- a fix that is not in the tree is not a fix. It now
# lives in the repo (versioned, reviewable) and is copied to
# D:\zo\_sentinel_tools\ for execution. -LogDir defaults OUTSIDE the repo so a
# repo-local invocation never writes run artifacts into a working tree that a
# deploy gate then measures as dirty.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Sha,
    [string]$Script = 'D:\zo\_sentinel_tools\verify_candidate.ps1',
    [string]$LogDir = 'D:\zo\_sentinel_tools\logs',
    [switch]$Wait,
    [int]$TimeoutMinutes = 30
)

$ErrorActionPreference = 'Stop'

if ($Sha -notmatch '^[0-9a-fA-F]{40}$') {
    throw "Sha must be a FULL 40-character hex SHA (got '$Sha'). An abbreviated SHA makes verify_candidate.ps1 prompt on stdin, and a detached prompt is unanswerable."
}

# NOTE the shared checkout D:\zo\zo-sentinel\zo-sentinel is deliberately NOT the
# default: it sits on branch verify-manifests, tens of commits behind, and often
# does not contain this script at all (FU-135/FU-128). D:\zo\_sentinel_tools
# holds a copy whose CONTENT is verified identical to ops/host/verify_candidate.ps1
# at the candidate SHA. Compare line-normalised, not by raw SHA-256: a Windows
# checkout stores CRLF and the blob is LF, so a byte hash reports a false
# mismatch (~1 byte per line) on two identical files. Re-verify when the repo
# copy changes.
if (-not (Test-Path $Script)) { throw "verify_candidate.ps1 not found at $Script" }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$runId  = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$outLog = Join-Path $LogDir "verify_$runId.out.log"
$errLog = Join-Path $LogDir "verify_$runId.err.log"
$rcFile = Join-Path $LogDir "verify_$runId.rc"

$pwshExe = (Get-Command powershell -ErrorAction SilentlyContinue).Source
if (-not $pwshExe) { throw 'powershell not found on PATH' }

# Written to a per-run .cmd rather than passed as an -ArgumentList string: every
# path on this box contains a space ("Zocomputer Agents"), and Start-Process
# re-quotes an argument that already contains double quotes, which silently
# mangles the command line and produces a run that dies instantly with no log at
# all (observed and fixed once already -- FU-112). A batch file has no quoting
# layer to lose. `setlocal enabledelayedexpansion` so !ERRORLEVEL! is read AFTER
# the child exits; %ERRORLEVEL% is substituted at PARSE time and would record
# the PRE-RUN value -- an rc file confidently reporting the previous run.
$batch = Join-Path $LogDir "verify_$runId.cmd"
@(
    '@echo off'
    'setlocal enabledelayedexpansion'
    ('"{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" -Sha {2} > "{3}" 2> "{4}"' -f $pwshExe, $Script, $Sha, $outLog, $errLog)
    ('echo RC=!ERRORLEVEL!> "{0}"' -f $rcFile)
) | Set-Content -Path $batch -Encoding ascii

$p = Start-Process -FilePath $batch -WorkingDirectory $LogDir -WindowStyle Hidden -PassThru

Write-Output "RUN_ID=$runId"
Write-Output "SHA=$Sha"
Write-Output "PID=$($p.Id)"
Write-Output "OUT=$outLog"
Write-Output "ERR=$errLog"
Write-Output "RC_FILE=$rcFile"

if (-not $Wait) {
    Write-Output 'LAUNCHED (detached). Poll OUT for progress and RC_FILE for the verdict.'
    Write-Output 'Verdict JSON also lands at "D:\zo\Zocomputer Agents\_deploy_evidence\verdict_latest.json".'
    exit 0
}

if (-not $p.WaitForExit($TimeoutMinutes * 60 * 1000)) {
    Write-Output "TIMEOUT after $TimeoutMinutes minutes; pid $($p.Id) left running"
    exit 3
}
$rc = (Get-Content $rcFile -ErrorAction SilentlyContinue) -join ''
Write-Output $rc
exit $p.ExitCode
