#!/usr/bin/env python3
"""
SFT Dataset Pre-Flight Validator
Runs before RunPod pod spin-up for training. Validates JSONL corpus before billing.
Checks: SCHEMA, TOKENIZER SIMULATION, VRAM ESTIMATE.
"""
import argparse
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from statistics import quantiles

SERVICE_NAME = "sft_dataset_preflight"
LOG_FILE = "/home/workspace/logs/sft_dataset_preflight.log"
DEFAULT_JSONL = "/home/workspace/shared/sft/data/train.jsonl"
DEFAULT_MAX_SEQ_LEN = 2048
DEFAULT_GPU_GB = 24
DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"
DEFAULT_BASE_PARAMS = 3.09e9
DEFAULT_HIDDEN_SIZE = 2048
PREFLIGHT_REPORT_PATH = "/home/workspace/shared/sft/preflight_report.json"

Qwen2_5_CONFIGS = {
    "Qwen/Qwen2.5-3B-Instruct": {"params": 3.09e9, "hidden_size": 2048},
    "Qwen/Qwen2.5-1.5B-Instruct": {"params": 1.5e9, "hidden_size": 2048},
    "Qwen/Qwen2.5-0.5B-Instruct": {"params": 0.5e9, "hidden_size": 1024},
    "Qwen/Qwen2.5-7B-Instruct": {"params": 7.0e9, "hidden_size": 3584},
    "Qwen/Qwen2.5-14B-Instruct": {"params": 14.0e9, "hidden_size": 5120},
}


def setup_logging():
    log_dir = Path(LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(SERVICE_NAME)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        logger.handlers.clear()
    handler = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    return logger


def load_jsonl(file_path: str) -> list[tuple[int, dict]]:
    rows = []
    with open(file_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append((idx, json.loads(line)))
            except json.JSONDecodeError as e:
                rows.append((idx, {"_parse_error": str(e), "_raw": line}))
    return rows


def check_schema(rows: list[tuple[int, dict]], logger: logging.Logger) -> tuple[bool, str, list[dict]]:
    logger.info("CHECK 1: SCHEMA validation starting")
    failures = []
    required_roles = {"system", "user", "assistant"}

    for idx, row in rows:
        if "_parse_error" in row:
            failures.append({
                "row": idx,
                "error": f"JSON parse failed: {row['_parse_error']}",
                "type": "parse_error"
            })
            continue

        if not isinstance(row, dict):
            failures.append({
                "row": idx,
                "error": f"Row is not a dict, got {type(row).__name__}",
                "type": "type_error"
            })
            continue

        if "messages" not in row:
            failures.append({
                "row": idx,
                "error": "Missing 'messages' field",
                "type": "missing_field"
            })
            continue

        messages = row["messages"]
        if not isinstance(messages, list):
            failures.append({
                "row": idx,
                "error": f"'messages' is not a list, got {type(messages).__name__}",
                "type": "type_error"
            })
            continue

        if len(messages) < 3:
            failures.append({
                "row": idx,
                "error": f"'messages' has {len(messages)} items, need at least 3 (system+user+assistant)",
                "type": "insufficient_messages"
            })
            continue

        present_roles = set()
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                failures.append({
                    "row": idx,
                    "error": f"messages[{i}] is not a dict, got {type(msg).__name__}",
                    "type": "type_error"
                })
                break

            if "role" not in msg:
                failures.append({
                    "row": idx,
                    "error": f"messages[{i}] missing 'role' field",
                    "type": "missing_field"
                })
                break

            if "content" not in msg:
                failures.append({
                    "row": idx,
                    "error": f"messages[{i}] missing 'content' field",
                    "type": "missing_field"
                })
                break

            role = msg.get("role")
            content = msg.get("content")

            if role not in required_roles and role not in ("tool", "function"):
                failures.append({
                    "row": idx,
                    "error": f"messages[{i}] has unrecognized role '{role}'",
                    "type": "invalid_role"
                })
                break

            if content is None:
                failures.append({
                    "row": idx,
                    "error": f"messages[{i}] 'content' is null",
                    "type": "null_content"
                })
                break

            if not isinstance(content, str):
                failures.append({
                    "row": idx,
                    "error": f"messages[{i}] 'content' is not a string, got {type(content).__name__}",
                    "type": "type_error"
                })
                break

            present_roles.add(role)

        if failures and failures[-1]["row"] == idx:
            continue

        missing_roles = required_roles - present_roles
        if missing_roles:
            failures.append({
                "row": idx,
                "error": f"Missing required roles: {missing_roles}",
                "type": "missing_role"
            })

    if failures:
        first = failures[0]
        logger.error(f"SCHEMA check FAILED at row {first['row']}: {first['error']}")
        return False, f"Schema validation failed at row {first['row']}: {first['error']}", failures

    total = sum(1 for idx, row in rows if "_parse_error" not in row)
    logger.info(f"SCHEMA check PASSED ({total} valid rows)")
    return True, f"SCHEMA check passed ({total} rows)", []


def check_tokenizer_simulation(
    rows: list[tuple[int, dict]],
    max_seq_len: int,
    logger: logging.Logger
) -> tuple[bool, str, dict]:
    logger.info("CHECK 2: TOKENIZER SIMULATION starting")
    logger.info("Loading tokenizer: Qwen/Qwen2.5-3B-Instruct")

    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(
            "Qwen/Qwen2.5-3B-Instruct",
            trust_remote_code=True,
            use_fast=False
        )
    except Exception as e:
        logger.error(f"Failed to load tokenizer: {e}")
        return False, f"Tokenizer load failed: {e}", {}

    token_lengths = []
    failures = []
    cap_failures = []

    for idx, row in rows:
        if "_parse_error" in row:
            continue

        try:
            messages = row.get("messages", [])
            chat_text = tokenizer.apply_chat_template(messages, tokenize=False)
            tokens = tokenizer.encode(chat_text, add_special_tokens=True)
            length = len(tokens)
            token_lengths.append(length)

            if length > max_seq_len:
                cap_failures.append({
                    "row": idx,
                    "length": length,
                    "exceeded_by": length - max_seq_len
                })

        except Exception as e:
            failures.append({
                "row": idx,
                "error": str(e)
            })

    if failures:
        first = failures[0]
        logger.error(f"Tokenization failed at row {first['row']}: {first['error']}")
        return False, f"Tokenization failed at row {first['row']}: {first['error']}", {}

    if not token_lengths:
        logger.warning("No token lengths recorded")
        return False, "No valid examples to tokenize", {}

    total = len(token_lengths)
    sorted_lengths = sorted(token_lengths)
    p50 = sorted_lengths[total // 2]
    p75 = sorted_lengths[int(total * 0.75)]
    p90 = sorted_lengths[int(total * 0.90)]
    p95 = sorted_lengths[int(total * 0.95)]
    p99 = sorted_lengths[int(total * 0.99)]
    pmax = sorted_lengths[-1]
    pmin = sorted_lengths[0]
    mean_len = sum(token_lengths) / total

    threshold_85 = max_seq_len * 0.85

    quartile_counts = {
        "Q1 (<=P25)": sum(1 for l in sorted_lengths if l <= sorted_lengths[int(total * 0.25)]),
        "Q2 (P25-P50)": sum(1 for l in sorted_lengths if sorted_lengths[int(total * 0.25)] < l <= p50),
        "Q3 (P50-P75)": sum(1 for l in sorted_lengths if p50 < l <= p75),
        "Q4 (P75-P90)": sum(1 for l in sorted_lengths if p75 < l <= p90),
        "Q5 (P90-P95)": sum(1 for l in sorted_lengths if p90 < l <= p95),
        "Q6 (P95-P99)": sum(1 for l in sorted_lengths if p95 < l <= p99),
        "Q7 (>P99)": sum(1 for l in sorted_lengths if l > p99),
    }

    distribution = {
        "count": total,
        "min": pmin,
        "max": pmax,
        "mean": round(mean_len, 1),
        "p50": p50,
        "p75": p75,
        "p90": p90,
        "p95": p95,
        "p99": p99,
        "quartile_counts": quartile_counts,
        "cap_failures": len(cap_failures),
    }

    logger.info(f"Token distribution: min={pmin}, p50={p50}, p95={p95}, max={pmax}")
    logger.info(f"95th percentile threshold (85% of cap): {threshold_85:.0f}")

    if cap_failures:
        first = cap_failures[0]
        logger.error(f"SCHEMA+TOKEN check FAILED: row {first['row']} exceeds cap by {first['exceeded_by']} tokens")
        return False, f"Token length exceeds MAX_SEQ_LEN at row {first['row']} ({first['length']} > {max_seq_len})", distribution

    if p95 > threshold_85:
        logger.warning(f"P95 token length {p95} exceeds 85% threshold {threshold_85:.0f} — corpus straining packer")
        return False, f"P95 ({p95}) > 85% of cap ({threshold_85:.0f}) — corpus straining packer", distribution

    logger.info(f"TOKENIZER SIMULATION check PASSED (P95={p95}, max={pmax})")
    return True, f"Tokenizer check passed (P95={p95}, max={pmax})", distribution


def compute_vram_estimate(
    target_gpu_gb: float,
    base_model: str,
    logger: logging.Logger
) -> tuple[bool, str, dict]:
    logger.info("CHECK 3: VRAM ESTIMATE starting")
    config = Qwen2_5_CONFIGS.get(base_model, {"params": DEFAULT_BASE_PARAMS, "hidden_size": DEFAULT_HIDDEN_SIZE})
    params = config["params"]
    hidden_size = config["hidden_size"]
    batch_size = 1
    max_seq_len = DEFAULT_MAX_SEQ_LEN

    weights_bf16 = params * 2
    optimizer_8bit = params * 1
    lora_ratio = 0.005
    gradients_bf16 = params * lora_ratio * 2
    activations = batch_size * max_seq_len * hidden_size * 4

    total_bytes = weights_bf16 + optimizer_8bit + gradients_bf16 + activations
    total_gb = total_bytes / (1024 ** 3)
    headroom_gb = target_gpu_gb - total_gb
    headroom_pct = (headroom_gb / target_gpu_gb) * 100
    effective_headroom = target_gpu_gb * 0.85
    threshold_gb = target_gpu_gb * 0.85

    breakdown = {
        "base_model": base_model,
        "params_B": round(params / 1e9, 2),
        "hidden_size": hidden_size,
        "target_gpu_gb": target_gpu_gb,
        "headroom_target_pct": 15,
        "components": {
            "weights_bf16_GB": round(weights_bf16 / (1024**3), 2),
            "optimizer_8bit_GB": round(optimizer_8bit / (1024**3), 2),
            "gradients_bf16_GB": round(gradients_bf16 / (1024**3), 2),
            "activations_GB": round(activations / (1024**3), 2),
        },
        "total_gb": round(total_gb, 2),
        "headroom_gb": round(headroom_gb, 2),
        "headroom_pct": round(headroom_pct, 1),
        "threshold_gb_85pct": round(threshold_gb, 2),
        "passes": total_gb <= threshold_gb,
    }

    logger.info(f"VRAM breakdown for {base_model} on {target_gpu_gb}GB GPU:")
    for comp, val in breakdown["components"].items():
        logger.info(f"  {comp}: {val} GB")
    logger.info(f"  Total: {breakdown['total_gb']} GB")
    logger.info(f"  Headroom: {breakdown['headroom_gb']} GB ({breakdown['headroom_pct']}%)")

    if total_gb > threshold_gb:
        logger.error(f"VRAM check FAILED: {total_gb:.2f} GB > threshold {threshold_gb:.2f} GB")
        return False, f"VRAM estimate {total_gb:.2f}GB exceeds 85% threshold ({threshold_gb:.2f}GB)", breakdown

    logger.info(f"VRAM check PASSED (total={total_gb:.2f}GB, headroom={headroom_gb:.2f}GB)")
    return True, f"VRAM check passed ({total_gb:.2f}GB, {headroom_gb:.2f}GB headroom)", breakdown


def write_report(report: dict, logger: logging.Logger):
    report_dir = Path(PREFLIGHT_REPORT_PATH).parent
    report_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", dir=report_dir, suffix=".json", delete=False
    ) as tmp:
        json.dump(report, tmp, indent=2)
        tmp_path = tmp.name

    Path(tmp_path).rename(PREFLIGHT_REPORT_PATH)
    logger.info(f"Report written to {PREFLIGHT_REPORT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="SFT Dataset Pre-Flight Validator")
    parser.add_argument(
        "--jsonl",
        default=DEFAULT_JSONL,
        help=f"Path to JSONL training corpus (default: {DEFAULT_JSONL})"
    )
    parser.add_argument(
        "--max-seq-len",
        type=int,
        default=DEFAULT_MAX_SEQ_LEN,
        help=f"Maximum sequence length (default: {DEFAULT_MAX_SEQ_LEN})"
    )
    parser.add_argument(
        "--gpu-gb",
        type=float,
        default=DEFAULT_GPU_GB,
        help=f"Target GPU memory in GB (default: {DEFAULT_GPU_GB})"
    )
    parser.add_argument(
        "--base-model",
        default=DEFAULT_BASE_MODEL,
        help=f"Base model name (default: {DEFAULT_BASE_MODEL})"
    )
    args = parser.parse_args()

    logger = setup_logging()
    logger.info(f"=== SFT Dataset Pre-Flight Validation Started ===")
    logger.info(f"JSONL: {args.jsonl}")
    logger.info(f"MAX_SEQ_LEN: {args.max_seq_len}")
    logger.info(f"GPU_GB: {args.gpu_gb}")
    logger.info(f"BASE_MODEL: {args.base_model}")

    if not os.path.exists(args.jsonl):
        logger.error(f"JSONL file not found: {args.jsonl}")
        print(f"ERROR: JSONL file not found: {args.jsonl}", file=sys.stderr)
        sys.exit(1)

    rows = load_jsonl(args.jsonl, logger)
    total_rows = len(rows)
    logger.info(f"Loaded {total_rows} rows from {args.jsonl}")

    schema_ok, schema_msg, schema_failures = check_schema(rows, logger)
    token_ok, token_msg, token_distribution = check_tokenizer_simulation(
        rows, args.max_seq_len, logger
    )
    vram_ok, vram_msg, vram_breakdown = compute_vram_estimate(
        args.gpu_gb, args.base_model, logger
    )

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "jsonl_path": args.jsonl,
        "max_seq_len": args.max_seq_len,
        "gpu_gb": args.gpu_gb,
        "base_model": args.base_model,
        "total_rows": total_rows,
        "checks": {
            "schema": {
                "passed": schema_ok,
                "message": schema_msg,
                "failures": schema_failures[:10],
                "failure_count": len(schema_failures),
            },
            "tokenizer": {
                "passed": token_ok,
                "message": token_msg,
                "distribution": token_distribution,
            },
            "vram": {
                "passed": vram_ok,
                "message": vram_msg,
                "breakdown": vram_breakdown,
            },
        },
        "all_passed": schema_ok and token_ok and vram_ok,
    }

    write_report(report, logger)

    print("\n" + "=" * 60)
    print("SFT DATASET PRE-FLIGHT REPORT")
    print("=" * 60)
    print(f"\nJSONL: {args.jsonl}")
    print(f"Total rows: {total_rows}")
    print(f"\n[CHECK 1] SCHEMA: {'PASS' if schema_ok else 'FAIL'}")
    print(f"  {schema_msg}")
    if schema_failures:
        print(f"  First failure at row {schema_failures[0]['row']}: {schema_failures[0]['error']}")

    print(f"\n[CHECK 2] TOKENIZER: {'PASS' if token_ok else 'FAIL'}")
    print(f"  {token_msg}")
    if token_distribution:
        dist = token_distribution
        print(f"  Length distribution (tokens):")
        print(f"    min={dist.get('min', 0)}, p50={dist.get('p50', 0)}, p95={dist.get('p95', 0)}, max={dist.get('max', 0)}")
        print(f"  Quartile counts:")
        for qname, qcount in dist.get("quartile_counts", {}).items():
            print(f"    {qname}: {qcount}")
        if dist.get("cap_failures", 0) > 0:
            print(f"  EXCEEDS CAP: {dist['cap_failures']} examples")

    print(f"\n[CHECK 3] VRAM ESTIMATE: {'PASS' if vram_ok else 'FAIL'}")
    print(f"  {vram_msg}")
    if vram_breakdown:
        print(f"  Breakdown:")
        for comp, val in vram_breakdown.get("components", {}).items():
            print(f"    {comp}: {val} GB")
        print(f"  Total: {vram_breakdown.get('total_gb', 0)} GB / {vram_breakdown.get('target_gpu_gb', 0)} GB target")
        print(f"  Headroom: {vram_breakdown.get('headroom_gb', 0)} GB ({vram_breakdown.get('headroom_pct', 0)}%)")
        print(f"  85% threshold: {vram_breakdown.get('threshold_gb_85pct', 0)} GB")

    print("\n" + "=" * 60)
    if report["all_passed"]:
        print("RESULT: ALL CHECKS PASSED")
        print("Ready to proceed with RunPod training.")
    else:
        print("RESULT: ONE OR MORE CHECKS FAILED")
        print("Do NOT spin up RunPod until issues are resolved.")
    print("=" * 60 + "\n")

    if not report["all_passed"]:
        sys.exit(1)

    logger.info("=== SFT Dataset Pre-Flight Validation COMPLETED ===")
    sys.exit(0)


if __name__ == "__main__":
    main()