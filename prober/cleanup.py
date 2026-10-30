#!/usr/bin/env python3
"""Delete raw run files older than --keep-days (default 30).

Raw files live in raw/YYYY-MM-DD/ under --data-dir. A whole day folder is
deleted once its date is more than --keep-days before today (UTC). Folders
whose names are not dates, and everything outside raw/, are left alone, and
daily.json is never touched.

Usage:
  python cleanup.py --data-dir ./data [--keep-days 30]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone

DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def cleanup(data_dir: str, keep_days: int = 30, today: date | None = None) -> list:
    """Delete old day folders. Returns the names of the folders deleted."""
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=keep_days)
    raw_dir = os.path.join(data_dir, 'raw')
    if not os.path.isdir(raw_dir):
        return []
    deleted = []
    for name in sorted(os.listdir(raw_dir)):
        path = os.path.join(raw_dir, name)
        if not DAY_RE.match(name) or not os.path.isdir(path) or os.path.islink(path):
            continue
        try:
            day = date.fromisoformat(name)
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(path)
            deleted.append(name)
    return deleted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Delete old raw run files')
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--keep-days', type=int, default=30)
    args = parser.parse_args(argv)
    deleted = cleanup(args.data_dir, args.keep_days)
    print(f'deleted {len(deleted)} day folder(s)' + (f": {', '.join(deleted)}" if deleted else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
