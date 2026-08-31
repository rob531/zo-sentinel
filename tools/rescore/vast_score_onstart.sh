#!/usr/bin/env bash
# vast_score_onstart.sh -- GPU scoring pass for zo-sentinel (NOT training).
# Clones the SFT repo (code), pulls the transfer bundle (adapter + inputs) from
# SCORE_BRANCH, runs scripts/eval_phase2.py --device cuda over the inputs, and
# pushes preds.jsonl.gz + the log to RESULTS_BRANCH. Self-contained via PAT.
set -uo pipefail
mkdir -p /workspace
exec > >(tee -a /workspace/onstart.log) 2>&1
echo "===== score-onstart $(date -u +%FT%TZ) ====="
fail(){
  echo "[score-onstart] FATAL: $*"; echo "===== SCORE_FAIL ====="
  # best-effort: push the log to a -fail branch so the tower can debug without SSH
  if cd /workspace/repo 2>/dev/null; then
    git checkout --orphan "${RESULTS_BRANCH:-score}-fail" >/dev/null 2>&1
    git rm -rf . >/dev/null 2>&1 || true
    mkdir -p score_results; cp /workspace/onstart.log score_results/ 2>/dev/null || true
    git add score_results >/dev/null 2>&1
    git -c user.email=pod@vast -c user.name=score-pod commit -q -m "score FAIL log" >/dev/null 2>&1
    git push "https://x-access-token:${RUnpodGHAPI}@github.com/rob531/zomesh-sentinel-sft.git" \
        "HEAD:refs/heads/${RESULTS_BRANCH:-score}-fail" >/dev/null 2>&1 || true
  fi
  sleep 30; exit 1; }

: "${RUnpodGHAPI:?}"   || fail "RUnpodGHAPI missing"
: "${SCORE_BRANCH:?}"  || fail "SCORE_BRANCH missing"
: "${RESULTS_BRANCH:?}" || fail "RESULTS_BRANCH missing"
echo "[score-onstart] SCORE_BRANCH=$SCORE_BRANCH RESULTS_BRANCH=$RESULTS_BRANCH PAT=${RUnpodGHAPI:0:4}..(${#RUnpodGHAPI})"

REPO_URL="https://x-access-token:${RUnpodGHAPI}@github.com/rob531/zomesh-sentinel-sft.git"

echo "[score-onstart] apt prereqs"
apt-get update -qq && apt-get install -y -qq git curl >/dev/null 2>&1 || fail "apt"

cd /workspace
# FU-321 (2026-08-11): these two used to be `>/dev/null 2>&1 || fail "..."`, so a
# transient network blip and an expired credential produced the SAME 356-byte
# forensics log -- "FATAL: fetch bundle", no git output, nothing to act on. It cost
# a tower-side reproduction to learn that the branch, the PAT and the bundle were
# all fine and the machine was not. Keep git's own words, and retry: the failure
# that bit was transient, and one retry would have cost nothing and saved the wave.
gitretry(){  # gitretry <label> <git args...>
  local label="$1"; shift
  local try rc
  for try in 1 2 3 4 5; do
    if git "$@" 2>/tmp/git_err.txt; then
      [ "$try" -gt 1 ] && echo "[score-onstart] $label OK on attempt $try"
      return 0
    fi
    rc=$?
    echo "[score-onstart] $label attempt $try/5 FAILED rc=$rc; git said:"
    sed 's/^/    | /' /tmp/git_err.txt || true
    sleep $(( try * 10 ))
  done
  return 1
}
echo "[score-onstart] clone repo (code)"
gitretry "clone" clone --depth 1 "$REPO_URL" repo || fail "clone (5 attempts, see git output above)"
cd /workspace/repo
echo "[score-onstart] fetch transfer bundle from $SCORE_BRANCH"
gitretry "fetch bundle" fetch --depth 1 origin "$SCORE_BRANCH" \
    || fail "fetch bundle (5 attempts, see git output above)"
git checkout FETCH_HEAD -- score_transfer || fail "checkout bundle"
ls -la score_transfer score_transfer/adapter
gunzip -c score_transfer/inputs.jsonl.gz > /workspace/inputs.jsonl || fail "gunzip"
echo "[score-onstart] inputs: $(wc -l < /workspace/inputs.jsonl) lines"

echo "[score-onstart] pip install (pinned, eval subset)"
pip install --no-cache-dir --quiet \
    "transformers==4.46.3" "peft==0.14.0" "datasets>=3.0,<4.0" \
    "accelerate>=1.0,<2.0" "safetensors>=0.4,<0.5" sentencepiece tqdm "numpy<2" \
    || fail "pip"
python -c "import torch,transformers,peft;print('torch',torch.__version__,'tf',transformers.__version__,'peft',peft.__version__)" || fail "imports"

echo "[score-onstart] GPU arch preflight"
python - <<'PY' || fail "gpu preflight"
import torch,sys
assert torch.cuda.is_available(), "cuda not available"
print("[arch] device=%s cap=%s torch=%s archlist=%s" % (
    torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0),
    torch.__version__, torch.cuda.get_arch_list()))
torch.zeros(8, device="cuda").sum().item()
PY

export BASE_MODEL="Qwen/Qwen2.5-3B" EMIT_PREDICTIONS_JSONL=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONPATH=/workspace/repo
# --- FU-091: HF-robust base-model pre-fetch (2026-07-24 hang fix) ---
# eval_phase2 from_pretrained("Qwen/Qwen2.5-3B") stalled ~2h at 0% GPU when the live
# HF download hung. Pre-fetch with a hard timeout + retries so a hang FAILS LOUD
# (SCORE_FAIL) in minutes instead of stalling to the 12h deadline; eval loads from cache.
export HF_HUB_DOWNLOAD_TIMEOUT=60
export BASE_MODEL="Qwen/Qwen2.5-3B"
echo "[score-onstart] pre-fetch base model $BASE_MODEL (hard timeout 600s x3)"
fetched=0
for attempt in 1 2 3; do
  if timeout 600 python - <<'PY'
import os
from huggingface_hub import snapshot_download
print("[prefetch] cached at", snapshot_download(os.environ["BASE_MODEL"]))
PY
  then fetched=1; break; fi
  echo "[score-onstart] base-model fetch attempt $attempt failed/timed out; retrying"; sleep 10
done
[ "$fetched" = "1" ] || fail "base-model fetch (HF hang/timeout after 3 tries)"

# --- FU-093: adapter-ARRIVAL gate (pod side). For 3 weeks the bundle shipped
# ONLY adapter_config.json (.gitignore ate *.safetensors/*.pt); eval then fell
# back to base + RANDOM HEADS and produced garbage that looked successful.
# Also catches the known 133-byte LFS-pointer failure class. Fail loud.
AD=score_transfer/adapter
[ -s "$AD/adapter_model.safetensors" ] || fail "adapter_model.safetensors MISSING in bundle"
[ -s "$AD/heads_state_dict.pt" ]       || fail "heads_state_dict.pt MISSING (heads would be RANDOM)"
_asz=$(stat -c%s "$AD/adapter_model.safetensors")
[ "$_asz" -ge 1000000 ] || fail "adapter is only ${_asz}B -- LFS pointer/stub, not weights"
echo "[score-onstart] adapter OK: ${_asz}B + heads $(stat -c%s "$AD/heads_state_dict.pt")B"

echo "[score-onstart] === eval_phase2 --device cuda (62k) start $(date -u +%FT%TZ) ==="
python scripts/eval_phase2.py \
    --adapter score_transfer/adapter \
    --base-model "Qwen/Qwen2.5-3B" \
    --eval-set /workspace/inputs.jsonl \
    --output-json /workspace/rpt.json \
    --predictions-jsonl /workspace/preds.jsonl \
    --device cuda || fail "eval_phase2"
# FU-093: eval_phase2 only WARNs when the adapter cannot attach, then scores on
# random heads. Treat those warnings as FATAL -- garbage must never reach import.
if grep -qE "could not attach adapter|heads have random init" /workspace/onstart.log; then
  fail "adapter did NOT attach (random heads) -- refusing to publish garbage scores"
fi
echo "[score-onstart] === eval done $(date -u +%FT%TZ); preds: $(wc -l < /workspace/preds.jsonl) lines ==="

gzip -c /workspace/preds.jsonl > /workspace/preds.jsonl.gz || fail "gzip preds"

echo "[score-onstart] push results -> $RESULTS_BRANCH"
git checkout --orphan "$RESULTS_BRANCH" >/dev/null 2>&1 || fail "orphan"
git rm -rf . >/dev/null 2>&1 || true
mkdir -p score_results
cp /workspace/preds.jsonl.gz score_results/
cp /workspace/onstart.log    score_results/ 2>/dev/null || true
cp /workspace/rpt.json       score_results/ 2>/dev/null || true
git add score_results
git -c user.email=pod@vast -c user.name=score-pod commit -q -m "score results ($RESULTS_BRANCH)" || fail "commit results"
PUSH_ERR=$(git push "$REPO_URL" "HEAD:refs/heads/$RESULTS_BRANCH" 2>&1) || {
  echo "[score-onstart] push results failed: ${PUSH_ERR}"
  echo "[score-onstart] fallback: chunked plain-git push (sidesteps LFS/size limits)"
  git rm -r --cached score_results >/dev/null 2>&1 || true
  rm -rf score_results; mkdir -p score_results
  cp /workspace/onstart.log score_results/ 2>/dev/null || true
  cp /workspace/rpt.json    score_results/ 2>/dev/null || true
  split -b 20m -d /workspace/preds.jsonl.gz score_results/preds.jsonl.gz.part.
  ( cd score_results && sha256sum preds.jsonl.gz.part.* ) > score_results/preds.sha256
  sha256sum /workspace/preds.jsonl.gz >> score_results/preds.sha256
  git add score_results
  git -c user.email=pod@vast -c user.name=score-pod commit -q -m "score results chunked ($RESULTS_BRANCH)" || fail "commit chunked"
  git push "$REPO_URL" "HEAD:refs/heads/$RESULTS_BRANCH" 2>&1 | tail -3
  git ls-remote "$REPO_URL" "refs/heads/$RESULTS_BRANCH" | grep -q . || fail "push results (chunked)"
}
echo "===== SCORE_DONE $(date -u +%FT%TZ) ====="
sleep 30
