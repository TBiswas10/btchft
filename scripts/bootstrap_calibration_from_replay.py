from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from btc_hft.backtest import BacktestEngine, ReplayTick, StrategyParams, load_replay_ticks
from btc_hft.decision_policy import calibrate_policy_from_outcomes
from btc_hft.config import load_and_validate_settings


def _ensure_env() -> None:
    os.environ.setdefault("ALPACA_API_KEY", "placeholder")
    os.environ.setdefault("ALPACA_SECRET_KEY", "placeholder")
    os.environ.setdefault("ALPACA_PAPER", "true")
    os.environ.setdefault("EXPECTANCY_DISABLE_ARTIFACT_LOAD", "true")


def run(args: argparse.Namespace) -> int:
    _ensure_env()

    source = Path(args.source)
    if not source.exists():
        raise FileNotFoundError(f"Replay source not found: {source}")

    settings = load_and_validate_settings()
    settings = replace(
        settings,
        analytics_window=max(settings.analytics_window, (args.limit + 10) if args.limit else settings.analytics_window),
    )

    ticks: list[ReplayTick] = load_replay_ticks(source, limit=args.limit)
    if len(ticks) < args.min_ticks:
        raise ValueError(f"Need at least {args.min_ticks} ticks, got {len(ticks)} from {source}")

    engine = BacktestEngine(
        settings=settings,
        strategy=StrategyParams(
            name=args.name,
            upgraded=True,
            as_gamma=settings.as_gamma,
            spread_vol_factor=settings.spread_vol_factor,
            spread_inventory_factor=settings.spread_inventory_factor,
            ofi_skew_bps=settings.ofi_skew_bps,
            min_net_edge_bps=args.min_net_edge_bps,
        ),
        seed=args.seed,
    )
    report = engine.run(ticks)

    outcomes = engine.analytics.decision_outcomes()
    if len(outcomes) < args.min_outcomes:
        raise ValueError(
            f"Need at least {args.min_outcomes} decision outcomes, got {len(outcomes)} from {source}"
        )

    artifact_dir = Path(args.output_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = calibrate_policy_from_outcomes(outcomes, output_dir=artifact_dir)

    print(f"SOURCE\t{source}")
    print(f"TICKS\t{len(ticks)}")
    print(f"TRADES\t{report.metrics.total_trades}")
    print(f"OUTCOMES\t{len(outcomes)}")
    print(f"ARTIFACT_VERSION\t{artifact.version}")
    print(f"ARTIFACT_DIR\t{artifact_dir}")
    print(f"REGIME_THRESHOLDS\t{artifact.to_dict().get('regime_params', {})}")
    print(f"BUCKET_THRESHOLDS\t{artifact.to_dict().get('regime_bucket_params', {})}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run replay and persist calibration artifacts from real outcomes."
    )
    parser.add_argument("--source", default="runtime/logs/bot.log", help="Replay log source")
    parser.add_argument("--limit", type=int, default=6000, help="Max replay ticks to load")
    parser.add_argument("--min-ticks", type=int, default=1000, help="Minimum ticks required")
    parser.add_argument("--min-outcomes", type=int, default=20, help="Minimum decision outcomes required")
    parser.add_argument("--min-net-edge-bps", type=float, default=1.0, help="Bootstrap threshold")
    parser.add_argument("--seed", type=int, default=7, help="Random seed")
    parser.add_argument("--name", default="calibration_bootstrap", help="Strategy label")
    parser.add_argument("--output-dir", default="runtime/calibration", help="Calibration artifact directory")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
