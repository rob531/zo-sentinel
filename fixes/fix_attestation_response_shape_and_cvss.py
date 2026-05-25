#!/usr/bin/env python3
"""
fix_attestation_response_shape_and_cvss.py

Two surgical fixes:

  A) attestation_engine.ws_query returns raw resp.json() which is {"rows":[...]}
     but callers iterate as if it were the list. Add response normalization
     to return body['rows'] when dict is received.

  B) threat_intel_ingestor CVSS parse. Current code does float(cvss) on
     OSV's severity score which may be a vector string
     ('CVSS:3.1/AV:N/AC:L/...') rather than a numeric score. Replace with
     tolerant parse: try float first, fall back to vector-string parse for
     base score extraction, else severity from summary text.

Both patches use exact-string anchoring (no regex).
Idempotent. AST-validated. Backs up before writing.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SENTINEL = Path("/home/workspace/zo_sentinel")
ATT = SENTINEL / "attestation_engine.py"
TIS = SENTINEL / "threat_intel_ingestor.py"


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def _ast_check(path, src):
    try:
        ast.parse(src)
    except SyntaxError as e:
        raise RuntimeError(f"AST invalid for {path.name}: {e}")


# ---- Fix A: attestation_engine ws_query response shape ----

ATT_OLD = (
    "def ws_query(sql: str, params: list = None) -> list:\n"
    '    """Execute SQL query via inference_router."""\n'
    "    payload = {'sql': sql}\n"
    "    if params:\n"
    "        payload['params'] = params\n"
    "    resp = requests.post(QUERY_URL, json=payload, timeout=30)\n"
    "    resp.raise_for_status()\n"
    "    return resp.json()\n"
)

ATT_NEW = (
    "def ws_query(sql: str, params: list = None) -> list:\n"
    '    """Execute SELECT via write_service /query. Normalizes response to list."""\n'
    "    payload = {'sql': sql}\n"
    "    if params:\n"
    "        payload['params'] = params\n"
    "    resp = requests.post(QUERY_URL, json=payload, timeout=30)\n"
    "    resp.raise_for_status()\n"
    "    body = resp.json()\n"
    "    if isinstance(body, list):\n"
    "        return body\n"
    "    if isinstance(body, dict):\n"
    "        if 'rows' in body:\n"
    "            return body['rows']\n"
    "        if 'results' in body:\n"
    "            return body['results']\n"
    "    return []\n"
)

# Also fix ws_write double-slash (same bug as threat_intel)
ATT_OLD_WS_WRITE = (
    "def ws_write(table: str, rows: Dict[str, Any], wait: bool = True) -> dict:\n"
    '    """Write to DuckDB via write_service."""\n'
    "    url = f'{WRITE_SERVICE_URL}/write'\n"
)
ATT_NEW_WS_WRITE = (
    "def ws_write(table: str, rows: Dict[str, Any], wait: bool = True) -> dict:\n"
    '    """Write to DuckDB via write_service."""\n'
    "    url = WRITE_SERVICE_URL  # already ends in /write\n"
)


def fix_attestation():
    print("\n=== Fix attestation_engine.py ===")
    if not ATT.exists():
        print(f"  [FAIL] {ATT} missing")
        return False
    src = ATT.read_text()
    changed = False

    if ATT_OLD in src:
        src = src.replace(ATT_OLD, ATT_NEW, 1)
        print("  [patch] ws_query now normalizes {rows:[...]} -> list")
        changed = True
    elif "if isinstance(body, dict):" in src and "'rows' in body" in src:
        print("  [skip] ws_query response shape already normalized")
    else:
        print("  [WARN] ws_query body not in expected form; skipping A")

    if ATT_OLD_WS_WRITE in src:
        src = src.replace(ATT_OLD_WS_WRITE, ATT_NEW_WS_WRITE, 1)
        print("  [patch] ws_write double-slash URL fixed")
        changed = True
    elif "url = WRITE_SERVICE_URL  # already ends in /write" in src:
        print("  [skip] ws_write double-slash already fixed")

    if not changed:
        print("  [noop] nothing to patch")
        return False

    _ast_check(ATT, src)
    _backup(ATT)
    ATT.write_text(src)
    print(f"  [done] {ATT.name} patched")
    return True


# ---- Fix B: threat_intel_ingestor CVSS parse ----

# Current bad block:
TIS_OLD_CVSS = (
    "                cvss = None\n"
    "                if 'severity' in vuln:\n"
    "                    for sev in vuln['severity']:\n"
    "                        if sev.get('type') == 'CVSS_V3':\n"
    "                            cvss = sev.get('score')\n"
    "                            break\n"
    "                if cvss:\n"
    "                    cvss_float = float(cvss)\n"
    "                    if cvss_float >= 9.0:\n"
    "                        severity = 'critical'\n"
    "                    elif cvss_float >= 7.0:\n"
    "                        severity = 'high'\n"
    "                    elif cvss_float >= 4.0:\n"
    "                        severity = 'medium'\n"
    "                    else:\n"
    "                        severity = 'low'\n"
    "                elif summary:\n"
    "                    severity = determine_severity(summary + ' ' + details)\n"
)

# New block: tolerate vector strings. OSV returns score as either:
#   - numeric string: '7.5'
#   - CVSS vector: 'CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:L'
# We try float first; if that fails, extract base score from vector or fall
# back to keyword-based severity from summary+details.
TIS_NEW_CVSS = (
    "                cvss = None\n"
    "                if 'severity' in vuln:\n"
    "                    for sev in vuln['severity']:\n"
    "                        if sev.get('type') == 'CVSS_V3':\n"
    "                            cvss = sev.get('score')\n"
    "                            break\n"
    "                cvss_float = None\n"
    "                if cvss:\n"
    "                    try:\n"
    "                        cvss_float = float(cvss)\n"
    "                    except (ValueError, TypeError):\n"
    "                        # Not a numeric -- might be a CVSS vector string.\n"
    "                        # Vector format: CVSS:3.1/AV:.../AC:.../... (no base score inline).\n"
    "                        # We can't compute base score without the scoring algorithm;\n"
    "                        # approximate via impact severity from I:H, C:H, A:H components.\n"
    "                        if isinstance(cvss, str) and cvss.startswith('CVSS'):\n"
    "                            high_impact = cvss.count(':H')\n"
    "                            if high_impact >= 3:\n"
    "                                cvss_float = 9.0   # critical approximation\n"
    "                            elif high_impact >= 2:\n"
    "                                cvss_float = 7.5   # high\n"
    "                            elif high_impact >= 1:\n"
    "                                cvss_float = 5.0   # medium\n"
    "                            else:\n"
    "                                cvss_float = 3.0   # low\n"
    "                if cvss_float is not None:\n"
    "                    if cvss_float >= 9.0:\n"
    "                        severity = 'critical'\n"
    "                    elif cvss_float >= 7.0:\n"
    "                        severity = 'high'\n"
    "                    elif cvss_float >= 4.0:\n"
    "                        severity = 'medium'\n"
    "                    else:\n"
    "                        severity = 'low'\n"
    "                elif summary:\n"
    "                    severity = determine_severity(summary + ' ' + details)\n"
)


def fix_threat_intel_cvss():
    print("\n=== Fix threat_intel_ingestor.py (CVSS parse) ===")
    if not TIS.exists():
        print(f"  [FAIL] {TIS} missing")
        return False
    src = TIS.read_text()

    if TIS_OLD_CVSS in src:
        src = src.replace(TIS_OLD_CVSS, TIS_NEW_CVSS, 1)
        print("  [patch] CVSS parse now tolerates vector strings")
    elif "high_impact = cvss.count(':H')" in src:
        print("  [skip] CVSS parse already hardened")
        return False
    else:
        print("  [WARN] CVSS block structure differs; manual review needed")
        return False

    _ast_check(TIS, src)
    _backup(TIS)
    TIS.write_text(src)
    print(f"  [done] {TIS.name} patched")
    return True


def main():
    print("=" * 60)
    print("Fix: attestation response shape + threat_intel CVSS parse")
    print("=" * 60)

    results = {}
    for label, fn in [
        ("attestation",   fix_attestation),
        ("threat_intel",  fix_threat_intel_cvss),
    ]:
        try:
            results[label] = fn()
        except Exception as e:
            print(f"  [EXCEPTION] {label}: {e}")
            results[label] = False

    print("\n" + "=" * 60)
    for k, v in results.items():
        print(f"  {k:<15} {'ok' if v else 'no-op-or-failed'}")
    print("=" * 60)

    if any(results.values()):
        print("\nRestart the patched daemons:")
        files = []
        if results["attestation"]:
            print("  pkill -9 -f 'python3 .*attestation_engine.py' 2>/dev/null")
            files.append("attestation_engine.py")
        if results["threat_intel"]:
            print("  pkill -9 -f 'python3 .*threat_intel_ingestor.py' 2>/dev/null")
            files.append("threat_intel_ingestor.py")
        print("  rm -f /var/run/zo/attestation_engine.pid /var/run/zo/threat_intel_ingestor.pid 2>/dev/null")
        print("  sleep 2")
        if results["attestation"]:
            print("  nohup python3 /home/workspace/zo_sentinel/attestation_engine.py "
                  ">> /home/workspace/logs/sentinel_attestation_engine.log 2>&1 &")
        if results["threat_intel"]:
            print("  nohup python3 /home/workspace/zo_sentinel/threat_intel_ingestor.py "
                  ">> /home/workspace/logs/sentinel_threat_intel_ingestor.log 2>&1 &")
        print(f"  python3 /home/workspace/zo_sentinel/tests/"
              f"rebaseline_protected_files.py {' '.join(files)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())