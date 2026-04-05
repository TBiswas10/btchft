from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _threshold_map(params: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in params.items():
        if isinstance(value, dict):
            out[key] = float(value.get("threshold_bps", 0.0))
    return out


def _compute_max_drift(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    return max(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def _window_slices(lines: list[str], window: int, step: int) -> list[tuple[int, int]]:
    slices: list[tuple[int, int]] = []
    start = 0
    while start < len(lines):
        end = min(start + window, len(lines))
        if end <= start:
            break
        slices.append((start, end))
        start += step
    return slices


def run(args: argparse.Namespace) -> int:
    source = Path(args.source)
    if not source.exists():
        raise FileNotFoundError(f"Replay source not found: {source}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    verify_script = Path(args.verify_script)
    if not verify_script.exists():
        raise FileNotFoundError(f"verify_policy_metrics.py not found: {verify_script}")

    lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        raise ValueError(f"Replay source is empty: {source}")

    windows = _window_slices(lines, args.window, args.step)
    if not windows:
        raise ValueError("No windows produced. Check --window/--step values.")

    print(f"SOURCE\t{source}")
    print(f"LINES\t{len(lines)}")
    print(f"WINDOWS\t{len(windows)}")
    print(f"OUT_DIR\t{out_dir}")

    for idx, (start, end) in enumerate(windows):
        window_file = out_dir / f"window_{idx:03d}.log"
        events_file = out_dir / f"window_{idx:03d}.events.tsv"
        summary_file = out_dir / f"window_{idx:03d}.summary.txt"

        window_file.write_text("\n".join(lines[start:end]) + "\n", encoding="utf-8")

        cmd = [
            sys.executable,
            str(verify_script),
            "--source",
            str(window_file),
            "--limit",
            str(args.window),
            "--seed",
            str(args.seed),
            "--preview-rows",
            str(args.preview_rows),
            "--events-out",
            str(events_file),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        summary_file.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")

        status = "ok" if proc.returncode == 0 else f"fail({proc.returncode})"
        print(f"WINDOW\t{idx}\t[{start}:{end}]\t{status}")

    calibration_dir = Path(args.calibration_dir)
    artifacts = sorted(calibration_dir.glob("expectancy_*.json")) if calibration_dir.exists() else []
    artifacts = artifacts[-2:]

    if len(artifacts) < 2:
        print(
            "GATE_RESULT\tWARN\t"
            f"Need >=2 calibration artifacts in {calibration_dir} for drift checks"
        )
        return 0

    max_regime_drift = 0.0
    max_bucket_drift = 0.0

    for prev_path, next_path in zip(artifacts[:-1], artifacts[1:]):
        prev_obj = json.loads(prev_path.read_text(encoding="utf-8"))
        next_obj = json.loads(next_path.read_text(encoding="utf-8"))

        prev_regime = _threshold_map(prev_obj.get("regime_params", {}))
        next_regime = _threshold_map(next_obj.get("regime_params", {}))
        prev_bucket = _threshold_map(prev_obj.get("regime_bucket_params", {}))
        next_bucket = _threshold_map(next_obj.get("regime_bucket_params", {}))

        max_regime_drift = max(max_regime_drift, _compute_max_drift(prev_regime, next_regime))
        max_bucket_drift = max(max_bucket_drift, _compute_max_drift(prev_bucket, next_bucket))

    pass_regime = max_regime_drift <= args.regime_gate_bps
    pass_bucket = max_bucket_drift <= args.bucket_gate_bps
    gate_status = "PASS" if (pass_regime and pass_bucket) else "FAIL"

    print(f"ARTIFACTS\t{len(artifacts)}")
    print(f"MAX_REGIME_DRIFT_BPS\t{max_regime_drift:.6f}")
    print(f"MAX_BUCKET_DRIFT_BPS\t{max_bucket_drift:.6f}")
    print(
        "GATE_THRESHOLDS_BPS\t"
        f"regime={args.regime_gate_bps:.2f}\tbucket={args.bucket_gate_bps:.2f}"
    )
    print(f"GATE_RESULT\t{gate_status}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run multi-window mixed-regime verification and compare calibration drift gates."
    )
    parser.add_argument("--source", default="runtime/logs/bot.log", help="Replay log source")
    parser.add_argument("--window", type=int, default=4000, help="Lines per window")
    parser.add_argument("--step", type=int, default=2000, help="Step size in lines")
    parser.add_argument(
        "--out-dir",
        default="runtime/backtests/mixed_regime",
        help="Directory for per-window slices and outputs",
    )
    parser.add_argument(
        "--verify-script",
        default="verify_policy_metrics.py",
        help="Path to verifier script",
    )
    parser.add_argument(
        "--calibration-dir",
        default="runtime/calibration",
        help="Directory containing expectancy_*.json artifacts",
    )
    parser.add_argument("--preview-rows", type=int, default=0, help="Preview rows for verifier")
    parser.add_argument("--seed", type=int, default=7, help="Random seed")
    parser.add_argument("--regime-gate-bps", type=float, default=0.35)
    parser.add_argument("--bucket-gate-bps", type=float, default=0.45)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
