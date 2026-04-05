from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore runtime sqlite database from backup")
    parser.add_argument("--backup", required=True, help="Backup sqlite file path")
    parser.add_argument("--db", default="runtime/trades.db", help="Destination sqlite DB path")
    args = parser.parse_args()

    backup = Path(args.backup)
    if not backup.exists():
        raise FileNotFoundError(f"Backup not found: {backup}")

    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, db)
    print(str(db))


if __name__ == "__main__":
    main()
