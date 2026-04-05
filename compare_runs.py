from __future__ import annotations

import argparse
import json
from pathlib import Path

from btc_hft.comparison import compare_trade_rows, load_actual_rows, load_expected_rows, summary_to_dict


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare expected backtest trades against paper-trading fills.")
    parser.add_argument("--expected", required=True, help="Path to expected trades CSV or JSON")
    parser.add_argument("--db", default="runtime/trades.db", help="Path to SQLite database with actual fills")
    parser.add_argument("--output", default="runtime/reports/comparison.json", help="Output report path")
    args = parser.parse_args()

    expected = load_expected_rows(Path(args.expected))
    actual = load_actual_rows(Path(args.db))
    summary = compare_trade_rows(expected, actual)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary_to_dict(summary), indent=2), encoding="utf-8")
    print(json.dumps(summary_to_dict(summary), indent=2))


if __name__ == "__main__":
    main()
