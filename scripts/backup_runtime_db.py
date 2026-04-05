from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup runtime sqlite database")
    parser.add_argument("--db", default="runtime/trades.db", help="Source sqlite DB path")
    parser.add_argument("--out-dir", default="runtime/backups", help="Backup directory")
    args = parser.parse_args()

    src = Path(args.db)
    if not src.exists():
        raise FileNotFoundError(f"DB not found: {src}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = out_dir / f"trades_{ts}.db"
    shutil.copy2(src, target)
    print(str(target))


if __name__ == "__main__":
    main()
