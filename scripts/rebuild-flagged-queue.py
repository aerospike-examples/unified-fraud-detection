#!/usr/bin/env python3
"""
Rebuild flagged_queue:index from the flagged_accounts KV set.

Run with loadgen paused so the scan completes quickly. After this, the backend
loads the review queue via batch_get on deterministic user_id keys — no feed
index or set scan on every API request.

Usage:
  python scripts/rebuild-flagged-queue.py
  AEROSPIKE_HOST=10.50.15.198 python scripts/rebuild-flagged-queue.py --limit 50000
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

from services.aerospike_service import aerospike_service  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Rebuild flagged_queue index from flagged_accounts")
    p.add_argument("--limit", type=int, default=100_000, help="Max flagged_accounts records to scan")
    args = p.parse_args()

    if not aerospike_service.connect():
        print("ERROR: could not connect to Aerospike", file=sys.stderr)
        return 1

    count = aerospike_service.rebuild_flagged_queue_index(limit=args.limit)
    print(f"Rebuilt flagged_queue:index with {count} user_ids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
