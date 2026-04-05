from __future__ import annotations

import argparse
import math
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

from btc_hft.backtest import BacktestEngine, StrategyParams, load_replay_ticks
from btc_hft.config import load_and_validate_settings


def _safe_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(stdev(values))


def _safe_corr(x: list[float], y: list[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    mx = mean(x)
    my = mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denx = math.sqrt(sum((a - mx) ** 2 for a in x))
    deny = math.sqrt(sum((b - my) ** 2 for b in y))
    den = denx * deny
    if den <= 1e-12:
        return 0.0
    return num / den


def _to_iso(ts_raw: str) -> str:
    try:
        return datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).isoformat()
    except Exception:
        return ts_raw


def _print_table(title: str, headers: list[str], rows: list[list[object]]) -> None:
    print(title)
    print("\t".join(headers))
    for row in rows:
        print("\t".join(str(v) for v in row))
    print()


def _regime_check(
    regime: str,
    avg_pred: float,
    avg_realized: float | None,
    trade_count: int,
    decision_count: int,
    signal_count: int,
) -> str:
    trade_rate = (trade_count / decision_count) if decision_count else 0.0
    signal_rate = (signal_count / decision_count) if decision_count else 0.0
    if regime in {"quiet", "high_vol"}:
        if signal_rate <= 0.02:
            return "PASS suppressed"
        return "WARN active_when_should_be_suppressed"

    if avg_pred > 0.05:
        if signal_count == 0:
            return "FAIL positive_expectancy_but_no_trades"
        if avg_realized is not None and avg_realized > 0:
            return "PASS positive_regime_trading"
        if trade_count == 0:
            return "WARN signaled_but_no_fills"
        return "WARN traded_but_non_positive_realized"

    if trade_rate <= 0.05:
        return "PASS low_edge_suppressed"
    return "WARN trading_in_non_positive_expectancy"


def run(args: argparse.Namespace) -> int:
    # Keep script runnable even when env creds are not configured locally.
    os.environ.setdefault("ALPACA_API_KEY", "placeholder")
    os.environ.setdefault("ALPACA_SECRET_KEY", "placeholder")
    os.environ.setdefault("ALPACA_PAPER", "true")

    settings = load_and_validate_settings()
    source = Path(args.source)
    ticks = load_replay_ticks(source, limit=args.limit)
    if not ticks:
        raise ValueError(f"No ticks loaded from {source}")

    # Prevent analytics windows from truncating the replay slice.
    settings = replace(settings, analytics_window=max(settings.analytics_window, len(ticks) + 10))

    engine = BacktestEngine(
        settings=settings,
        strategy=StrategyParams(
            name="verify_policy_metrics",
            upgraded=True,
            as_gamma=settings.as_gamma,
            spread_vol_factor=settings.spread_vol_factor,
            spread_inventory_factor=settings.spread_inventory_factor,
            ofi_skew_bps=settings.ofi_skew_bps,
            min_net_edge_bps=max(settings.min_net_edge_bps, 0.1),
        ),
        seed=args.seed,
    )
    report = engine.run(ticks)

    decisions = list(engine.analytics._decisions)
    trades = list(engine._trades)

    trade_realized_by_ts: dict[str, float] = {}
    trade_count_by_regime: dict[str, int] = {}
    for tr in trades:
        ts_key = _to_iso(tr.ts)
        realized_net_usd = tr.realized_pnl_usd - tr.fee_usd - tr.slippage_usd
        trade_realized_by_ts[ts_key] = trade_realized_by_ts.get(ts_key, 0.0) + realized_net_usd
        trade_count_by_regime[tr.regime] = trade_count_by_regime.get(tr.regime, 0) + 1

    per_timestamp_rows: list[dict] = []
    decision_count = min(len(decisions), len(ticks))
    for i in range(decision_count):
        d = decisions[i]
        ts = ticks[i].ts.isoformat()
        per_timestamp_rows.append(
            {
                "ts": ts,
                "regime": d.get("regime", "unknown"),
                "predicted_edge_bps": float(d.get("expected_net_bps", 0.0)),
                "confidence": float(d.get("confidence", 0.0)),
                "should_trade": bool(d.get("should_trade", False)),
                "threshold_used": float(d.get("threshold_bps", 0.0)),
                "reason": str(d.get("reason", "unknown")),
                "realized_pnl_usd": trade_realized_by_ts.get(ts),
            }
        )

    regimes = ["quiet", "normal", "trend", "high_vol", "warmup", "unknown"]
    summary_rows: list[list[object]] = []
    check_rows: list[list[object]] = []

    corr_x: list[float] = []
    corr_y: list[float] = []

    for regime in regimes:
        rows = [r for r in per_timestamp_rows if r["regime"] == regime]
        pred_vals = [float(r["predicted_edge_bps"]) for r in rows]
        realized_vals = [float(r["realized_pnl_usd"]) for r in rows if r["realized_pnl_usd"] is not None]
        regime_corr_x = [float(r["predicted_edge_bps"]) for r in rows if r["realized_pnl_usd"] is not None]
        regime_corr_y = [float(r["realized_pnl_usd"]) for r in rows if r["realized_pnl_usd"] is not None]
        should_trade_count = sum(1 for r in rows if r["should_trade"])

        for r in rows:
            if r["realized_pnl_usd"] is not None:
                corr_x.append(float(r["predicted_edge_bps"]))
                corr_y.append(float(r["realized_pnl_usd"]))

        avg_pred = mean(pred_vals) if pred_vals else 0.0
        std_pred = _safe_std(pred_vals)
        avg_realized = mean(realized_vals) if realized_vals else None
        std_realized = _safe_std(realized_vals)
        regime_corr = _safe_corr(regime_corr_x, regime_corr_y)
        trades_executed = trade_count_by_regime.get(regime, 0)

        summary_rows.append(
            [
                regime,
                len(rows),
                f"{avg_pred:.6f}",
                f"{std_pred:.6f}",
                "n/a" if avg_realized is None else f"{avg_realized:.6f}",
                "n/a" if avg_realized is None else f"{std_realized:.6f}",
                f"{regime_corr:.6f}",
                should_trade_count,
                trades_executed,
            ]
        )

        check_rows.append(
            [
                regime,
                trades_executed,
                len(rows),
                _regime_check(regime, avg_pred, avg_realized, trades_executed, len(rows), should_trade_count),
            ]
        )

    overall_corr = _safe_corr(corr_x, corr_y)

    print("VERIFY_POLICY_METRICS")
    print(f"source\t{source}")
    print(f"ticks_loaded\t{len(ticks)}")
    print(f"decisions_recorded\t{len(per_timestamp_rows)}")
    print(f"trades_executed\t{report.metrics.total_trades}")
    print(f"total_pnl_usd\t{report.metrics.total_pnl_usd:.6f}")
    print(f"overall_predicted_edge_vs_realized_pnl_corr\t{overall_corr:.6f}")
    print()

    _print_table(
        "PER_REGIME_SUMMARY",
        [
            "regime",
            "decision_count",
            "avg_predicted_edge_bps",
            "std_predicted_edge_bps",
            "avg_realized_pnl_usd",
            "std_realized_pnl_usd",
            "corr_pred_edge_vs_realized_pnl",
            "signals_should_trade",
            "total_trades_executed",
        ],
        summary_rows,
    )

    _print_table(
        "REGIME_BEHAVIOR_CHECKS",
        ["regime", "trades_executed", "decision_count", "check"],
        check_rows,
    )

    preview_n = min(args.preview_rows, len(per_timestamp_rows))
    preview_rows = []
    for r in per_timestamp_rows[:preview_n]:
        preview_rows.append(
            [
                r["ts"],
                r["regime"],
                f"{r['predicted_edge_bps']:.6f}",
                f"{r['confidence']:.6f}",
                str(r["should_trade"]),
                f"{r['threshold_used']:.6f}",
                r["reason"],
                "n/a" if r["realized_pnl_usd"] is None else f"{float(r['realized_pnl_usd']):.6f}",
            ]
        )

    _print_table(
        "PER_TIMESTAMP_PREVIEW",
        [
            "ts",
            "regime",
            "predicted_edge_bps",
            "confidence",
            "should_trade",
            "threshold_used",
            "reason",
            "realized_pnl_usd",
        ],
        preview_rows,
    )

    if args.events_out:
        out = Path(args.events_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "ts\tregime\tpredicted_edge_bps\tconfidence\tshould_trade\tthreshold_used\treason\trealized_pnl_usd"
        ]
        for r in per_timestamp_rows:
            lines.append(
                "\t".join(
                    [
                        r["ts"],
                        r["regime"],
                        f"{r['predicted_edge_bps']:.6f}",
                        f"{r['confidence']:.6f}",
                        str(r["should_trade"]),
                        f"{r['threshold_used']:.6f}",
                        r["reason"],
                        "" if r["realized_pnl_usd"] is None else f"{float(r['realized_pnl_usd']):.6f}",
                    ]
                )
            )
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"events_tsv_written\t{out}")

    if args.plots:
        try:
            import matplotlib.pyplot as plt  # type: ignore[import-not-found]

            if corr_x and corr_y:
                plt.figure(figsize=(6, 4))
                plt.scatter(corr_x, corr_y, alpha=0.5)
                plt.title("Predicted Edge (bps) vs Realized PnL (USD)")
                plt.xlabel("predicted_edge_bps")
                plt.ylabel("realized_pnl_usd")
                plt.tight_layout()
                plt.show()

            regime_names = [row[0] for row in summary_rows]
            regime_trades = [int(row[8]) for row in summary_rows]
            plt.figure(figsize=(7, 4))
            plt.bar(regime_names, regime_trades)
            plt.title("Trades Executed per Regime")
            plt.tight_layout()
            plt.show()

            conf_vals = [float(r["confidence"]) for r in per_timestamp_rows]
            if conf_vals:
                plt.figure(figsize=(6, 4))
                plt.hist(conf_vals, bins=20)
                plt.title("Confidence Distribution")
                plt.xlabel("confidence")
                plt.tight_layout()
                plt.show()
        except Exception as exc:
            print(f"plots_skipped\t{exc}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Verify regime-aware expectancy policy metrics on replay slices.")
    p.add_argument("--source", type=str, default="runtime/logs/bot.log", help="Replay source file (.log or .db)")
    p.add_argument("--limit", type=int, default=5000, help="Max ticks to load from source")
    p.add_argument("--seed", type=int, default=7, help="Random seed for dry-run fill simulation")
    p.add_argument("--preview-rows", type=int, default=30, help="How many per-timestamp rows to print")
    p.add_argument("--events-out", type=str, default="", help="Optional TSV file path for full per-timestamp rows")
    p.add_argument("--plots", action="store_true", help="Render optional verification plots")
    return p


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(run(args))
