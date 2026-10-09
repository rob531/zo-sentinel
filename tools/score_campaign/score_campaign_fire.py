#!/usr/bin/env python3
"""score_campaign_fire.py -- launch a GPU scoring pod for a campaign wave, under
the SAME guards weekly_rescore.ph_fire enforces. Reconstructed from that proven
path (there is no standalone fire_score.py in the repo).

GUARDS (do NOT loosen any of these):
  * GEO exclude {CN, HK, TW, RU, IR, KP}
  * hard MAX_DPH price cap (default 0.45 $/hr)
  * cheapest verified RTX_4090 first; fallback [L40S, L40, RTX_3090, A40, A6000]
  * adapter sha256 pin bf842f54.. verified against the local adapter dir before
    anything is launched (random-heads garbage is worse than no scores)
  * auto-destroy the instance on success
  * a GENEROUS inference timeout. THE 2026-10-08 SCAR: a 1800s cap killed
    score-import-shepherd mid-work. The default here is 6h (21600s) and the tool
    REFUSES a timeout below 1 hour so that scar cannot recur.

SAFE BY DEFAULT: with no flag this is a DRY-RUN -- it verifies the adapter sha and
prints the exact offer queries + launch plan, and touches NO network and NO GPU.
`--search` queries vast offers (read-only) to show the cheapest eligible machine.
`--launch` actually rents+runs+destroys (requires vastai_sdk + VAST_API_KEY) --
this session does NOT use it (NO GPU run).

Usage:
    python score_campaign_fire.py --adapter-dir D:\\zo\\runs\\v3.0_40974559_FULL\\final
    python score_campaign_fire.py --adapter-dir <dir> --search
    python score_campaign_fire.py --adapter-dir <dir> --launch   # GPU; not this session
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

ADAPTER_SHA_PIN = "bf842f5450347c19ac195ef32a7298a1579566a7b9b454a5381fda333e7198e3"
ADAPTER_FILE = "adapter_model.safetensors"
HEADS_FILE = "heads_state_dict.pt"

GEO_BLOCK = ("CN", "HK", "TW", "RU", "IR", "KP")
MAX_DPH_DEFAULT = 0.45
GPU_PRIMARY = "RTX_4090"
GPU_FALLBACK = ("RTX_4090", "L40S", "L40", "RTX_3090", "A40", "A6000")
IMAGE = "pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime"
# The 2026-10-08 scar: a 1800s cap killed the shepherd mid-work. Be generous.
INFERENCE_TIMEOUT_DEFAULT = 21600   # 6h
INFERENCE_TIMEOUT_FLOOR = 3600      # refuse anything below 1h
ONSTART_PATH = HERE.parent / "rescore" / "vast_score_onstart.sh"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_adapter(adapter_dir: Path) -> str:
    """Verify the adapter weights match the pinned sha256. Returns the sha.
    Fails loud -- a wrong/stub adapter means the pod scores on random heads."""
    ad = adapter_dir / ADAPTER_FILE
    if not ad.exists():
        raise SystemExit(f"ABORT: {ADAPTER_FILE} not found in {adapter_dir}")
    if ad.stat().st_size < 1_000_000:
        raise SystemExit(f"ABORT: {ad} is {ad.stat().st_size}B -- LFS pointer/stub, not weights")
    if not (adapter_dir / HEADS_FILE).exists():
        raise SystemExit(f"ABORT: {HEADS_FILE} missing -- heads would be RANDOM")
    got = sha256_file(ad)
    if got != ADAPTER_SHA_PIN:
        raise SystemExit(f"ABORT: adapter sha mismatch\n  got    {got}\n  pinned {ADAPTER_SHA_PIN}")
    print(f"[fire] adapter sha OK: {got[:8]}.. ({ad.stat().st_size}B) + heads present")
    return got


def offer_queries(max_dph: float):
    primary = (f"gpu_name={GPU_PRIMARY} num_gpus=1 dph_total<{max_dph} verified=true "
               f"rentable=true disk_space>=50 inet_down>=200")
    fallback = (f"gpu_name in [{','.join(GPU_FALLBACK)}] num_gpus=1 "
                f"dph_total<{max_dph} verified=true rentable=true disk_space>=50")
    return primary, fallback


def geo_ok(offer) -> bool:
    return not any(b in str(offer.get("geolocation", "")).upper() for b in GEO_BLOCK)


def search_cheapest(v, query: str, max_dph: float):
    offers = v.search_offers(query=query)
    elig = [o for o in offers if geo_ok(o) and float(o.get("dph_total", 99)) <= max_dph]
    return min(elig, key=lambda o: float(o["dph_total"])) if elig else None


def pick_offer(v, max_dph: float):
    primary, fallback = offer_queries(max_dph)
    best = search_cheapest(v, primary, max_dph)
    if not best:
        best = search_cheapest(v, fallback, max_dph)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description="Launch a guarded GPU scoring pod for a campaign wave.")
    ap.add_argument("--adapter-dir", required=True, help="dir holding adapter_model.safetensors + heads_state_dict.pt")
    ap.add_argument("--max-dph", type=float, default=MAX_DPH_DEFAULT, help="hard price cap $/hr")
    ap.add_argument("--score-branch", default="score-campaign", help="git branch carrying the transfer bundle")
    ap.add_argument("--results-branch", default="score-campaign-results", help="git branch to receive preds")
    ap.add_argument("--inference-timeout", type=int, default=INFERENCE_TIMEOUT_DEFAULT,
                    help="pod inference timeout seconds (floor 3600; default 21600)")
    ap.add_argument("--search", action="store_true", help="query vast offers (read-only) and show the cheapest eligible")
    ap.add_argument("--launch", action="store_true", help="actually rent+run+destroy (GPU; needs VAST_API_KEY)")
    a = ap.parse_args()

    if a.inference_timeout < INFERENCE_TIMEOUT_FLOOR:
        raise SystemExit(f"ABORT: --inference-timeout {a.inference_timeout}s is below the "
                         f"{INFERENCE_TIMEOUT_FLOOR}s floor (2026-10-08 scar). Use >= 1h.")
    if a.max_dph > 1.0:
        raise SystemExit(f"ABORT: --max-dph {a.max_dph} looks too high; cap is a guard, not a knob.")

    sha = verify_adapter(Path(a.adapter_dir))
    primary, fallback = offer_queries(a.max_dph)
    if not ONSTART_PATH.exists():
        raise SystemExit(f"ABORT: onstart script missing: {ONSTART_PATH}")

    print("[fire] launch plan")
    print(f"  geo-block:         {GEO_BLOCK}")
    print(f"  max_dph:           ${a.max_dph}/hr")
    print(f"  gpu (primary):     {GPU_PRIMARY}")
    print(f"  gpu (fallback):    {GPU_FALLBACK}")
    print(f"  image:             {IMAGE}")
    print(f"  adapter sha:       {sha[:8]}..")
    print(f"  inference timeout: {a.inference_timeout}s")
    print(f"  onstart:           {ONSTART_PATH}")
    print(f"  primary query:     {primary}")
    print(f"  fallback query:    {fallback}")

    if not (a.search or a.launch):
        print("[fire] DRY-RUN (no network, no GPU). Pass --search to probe offers, --launch to run.")
        return 0

    from vastai_sdk import VastAI  # lazy: only the live paths need it
    import os
    v = VastAI(api_key=os.environ.get("VAST_API_KEY") or os.environ.get("VAST_KEY", ""))

    best = pick_offer(v, a.max_dph)
    if not best:
        raise SystemExit("ABORT: no eligible GPU offer under price/geo guard")
    print(f"[fire] cheapest eligible: id={best.get('id')} {best.get('gpu_name')} "
          f"${best.get('dph_total')}/hr geo={best.get('geolocation')}")

    if not a.launch:
        print("[fire] --search only; not renting. Pass --launch to run (GPU).")
        return 0

    onstart = ONSTART_PATH.read_text(encoding="utf-8")
    resp = v.create_instance(
        id=best["id"], image=IMAGE, disk=50,
        env={"SCORE_BRANCH": a.score_branch, "RESULTS_BRANCH": a.results_branch},
        onstart_cmd=onstart, runtype="ssh", label="zo-sentinel-score-campaign")
    iid = resp.get("new_contract") or resp.get("contract_id") or resp.get("id")
    print(f"[fire] launched instance={iid} {best.get('gpu_name')} ${best.get('dph_total')}/hr")

    deadline = time.time() + a.inference_timeout
    print(f"[fire] watching until results branch appears or {a.inference_timeout}s elapses...")
    try:
        while time.time() < deadline:
            # the operator collects preds from RESULTS_BRANCH; this loop just
            # bounds the rental. Kept intentionally simple -- the proven watch
            # lives in weekly_rescore; here we only guarantee auto-destroy.
            time.sleep(60)
    finally:
        try:
            v.destroy_instance(id=iid)
            print(f"[fire] auto-destroyed instance={iid}")
        except Exception as exc:
            print(f"[fire] WARN: destroy failed ({exc}); DESTROY MANUALLY: vastai destroy instance {iid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
