#!/usr/bin/env python3
"""Idempotent: give watchdog.sh `_svc()` a default relaunch branch.
Before: `case $name in WriteService|InfRouter` only -- every other _svc entry
(RegistryApi, ApprovalWorkflow, ForensicDetail, BulkAssess, SearchApi,
ManualOverride) was pkill'd and NEVER relaunched, then logged RESTART FAILED
every tick (GH #4722/#4723: forensic_detail_api_v2 dead 2026-09-06 15:18Z+).
Usage: patch_svc_default_relaunch.py <watchdog.sh>   -> prints PATCHED|NOCHANGE, rc 0; rc 2 if anchor missing."""
import sys, re
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
MARK = "svc-default-relaunch"
if MARK in s:
    print("NOCHANGE"); sys.exit(0)
old = ("            InfRouter)    nohup python3 $MESH/inference_router_service.py >> $LOGS/inference_router.log 2>&1 & ;;\n"
       "        esac\n")
new = ("            InfRouter)    nohup python3 $MESH/inference_router_service.py >> $LOGS/inference_router.log 2>&1 & ;;\n"
       "            # svc-default-relaunch (2026-09-06, GH #4722): every other _svc entry is a\n"
       "            # $SENTINEL/<script>.py API launched by go.sh 12.10/12.10b. This branch was\n"
       "            # EMPTY before -- the watchdog pkill'd the service, relaunched nothing, and\n"
       "            # logged <name>_restart_FAILED every tick while registration_drift_check\n"
       "            # filed issues. Log names mirror go.sh so one service keeps one log.\n"
       "            *)  local _lg=${script%.py}\n"
       "                case $script in\n"
       "                    forensic_detail_api_v2.py) _lg=forensic_detail ;;\n"
       "                    bulk_assess_api.py)        _lg=bulk_assess ;;\n"
       "                    manual_override_api.py)    _lg=manual_override ;;\n"
       "                    approval_workflow.py)      _lg=approval ;;\n"
       "                esac\n"
       "                if [[ \"$script\" == *.py && -f \"$SENTINEL/$script\" ]]; then\n"
       "                    nohup python3 $SENTINEL/$script >> $LOGS/sentinel_${_lg}.log 2>&1 &\n"
       "                else\n"
       "                    log \"$name: no launch recipe for $script -- NOT relaunched\"\n"
       "                fi ;;\n"
       "        esac\n")
if s.count(old) != 1:
    print("ANCHOR-MISSING"); sys.exit(2)
s = s.replace(old, new)
s = re.sub(r"^# watchdog\.v3\.(\d+)", lambda m: "# watchdog.v3.%d" % (int(m.group(1)) + 1), s, count=1, flags=re.M)
open(p, "w", encoding="utf-8", newline="\n").write(s)
print("PATCHED")
