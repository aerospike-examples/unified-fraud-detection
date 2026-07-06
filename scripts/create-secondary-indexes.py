#!/usr/bin/env python3
"""
Create Aerospike secondary indexes used by the fraud demo backend.

Indexes are also created automatically on backend connect; run this script
once after a fresh cluster deploy or if indexes were dropped.

Usage:
  python scripts/create-secondary-indexes.py
  AEROSPIKE_HOST=10.50.15.198 python scripts/create-secondary-indexes.py
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

from services.aerospike_service import (  # noqa: E402
    IDX_FLAGGED_STATUS,
    IDX_INV_USER_ID,
    IDX_TXN_DAY,
    IDX_USER_WF_STATUS,
    SET_FLAGGED_ACCOUNTS,
    SET_INVESTIGATIONS,
    SET_TRANSACTIONS,
    SET_USERS,
    aerospike_service,
)


def main() -> int:
    if not aerospike_service.connect():
        print("ERROR: could not connect to Aerospike", file=sys.stderr)
        return 1

    aerospike_service.create_secondary_indexes()
    print("Secondary indexes ensured:")
    print(f"  {IDX_TXN_DAY}          -> {SET_TRANSACTIONS}.day")
    print(f"  {IDX_INV_USER_ID}      -> {SET_INVESTIGATIONS}.user_id")
    print(f"  {IDX_USER_WF_STATUS}   -> {SET_USERS}.wf_status")
    print(f"  {IDX_FLAGGED_STATUS}   -> {SET_FLAGGED_ACCOUNTS}.status")
    print("(flagged_queue pointer list is separate — use scripts/rebuild-flagged-queue.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
