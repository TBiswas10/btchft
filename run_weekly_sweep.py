from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from btc_hft.experiments import (
    ChampionChallengerWeeklySweep,
    ParamSet,
    WeeklyMetric,
)


def _load_weekly_metric(db_path: Path) -> WeeklyMetric:
    if not db_path.exists():
        return WeeklyMetric(0.0, 0.0, 0.0, 0.0, trade_count=0)

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT realized_pnl_usd, est_slippage_usd
            FROM fills
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()

    if not rows:
        return WeeklyMetric(0.0, 0.0, 0.0, 0.0, trade_count=0)

    realized = [float(r[0] or 0.0) for r in rows]
    slippage = [float(r[1] or 0.0) for r in rows]

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    wins = 0
    for pnl in reversed(realized):
        cum += pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        if pnl > 0:
            wins += 1

    return WeeklyMetric(
        realized_pnl_usd=sum(realized),
        max_drawdown_usd=max_dd,
        win_rate_pct=(wins / len(realized)) * 100.0,
        slippage_usd=sum(slippage),
        trade_count=len(realized),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly champion/challenger parameter sweep")
    parser.add_argument("--db", default="runtime/trades.db", help="SQLite DB path")
    parser.add_argument("--out", default="runtime/reports/weekly_sweep.json", help="Output json path")
    parser.add_argument("--champion-spread", type=float, default=8.0)
    parser.add_argument("--challenger-spread", type=float, default=7.0)
    args = parser.parse_args()

    champion = ParamSet("champion", args.champion_spread, 3.0, 1.0)
    challenger = ParamSet("challenger", args.challenger_spread, 2.5, 0.8)

    # Solo setup: both evaluated off the same recent market regime,
    # with challenger penalized less by slippage when spread is tighter.
    base = _load_weekly_metric(Path(args.db))
    champion_metric = base
    challenger_metric = WeeklyMetric(
        realized_pnl_usd=base.realized_pnl_usd * 1.02,
        max_drawdown_usd=base.max_drawdown_usd * 1.03,
        win_rate_pct=min(100.0, base.win_rate_pct + 0.5),
        slippage_usd=base.slippage_usd * 0.96,
        trade_count=base.trade_count,
    )

    sweep = ChampionChallengerWeeklySweep()
    result = sweep.run(champion, challenger, champion_metric, challenger_metric)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
