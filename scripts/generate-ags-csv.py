#!/usr/bin/env python3
"""
Generate Aerospike Graph bulk-load CSVs at scale (streaming, sharded, parallel).

Deterministic IDs: User1, Account1, Device1, ...
Each user: 1 account, 1 device, OWNS/USES edges, and 1–5 TRANSACTS edges.

Output is sharded part files under data/graph_csv/ for parallel bulk load.
"""

from __future__ import annotations

import argparse
import csv
import multiprocessing
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ACCOUNT_TYPES = ["checking", "savings", "credit"]
TXN_TYPES = ["transfer", "payment", "purchase", "withdrawal", "deposit"]
TXN_METHODS = ["electronic_transfer", "wire_transfer", "ach", "card"]
LOCATIONS = [
    "New York, NY", "Chicago, IL", "Austin, TX", "Seattle, WA",
    "Los Angeles, CA", "Denver, CO", "Boston, MA", "Miami, FL",
]
BANKS = ["Chase Bank", "Wells Fargo", "Bank of America", "Citibank", "U.S. Bank"]
DEVICE_TYPES = ["mobile", "desktop", "tablet"]
DEVICE_OS = {
    "mobile": ["iOS 17", "Android 14"],
    "desktop": ["Windows 11", "macOS Sonoma"],
    "tablet": ["iPadOS 17", "Android 13"],
}
DEVICE_BROWSERS = {
    "mobile": ["Safari Mobile", "Chrome Mobile"],
    "desktop": ["Chrome", "Firefox", "Safari", "Edge"],
    "tablet": ["Safari", "Chrome"],
}

BATCH_FLUSH = 50_000

USER_FIELDS = [
    "~id", "~label", "name:String", "email:String", "phone:String",
    "age:Int", "location:String", "occupation:String",
    "risk_score:Double", "signup_date:Date",
]
ACCOUNT_FIELDS = [
    "~id", "~label", "type:String", "balance:Double", "bank_name:String",
    "status:String", "created_date:Date", "fraud_flag:Boolean",
]
DEVICE_FIELDS = [
    "~id", "~label", "type:String", "os:String", "browser:String",
    "fingerprint:String", "first_seen:Date", "last_login:Date",
    "login_count:Int", "fraud_flag:Boolean",
]
OWNS_FIELDS = ["~from", "~to", "~label", "since:Date"]
USES_FIELDS = ["~from", "~to", "~label", "first_used:Date", "last_used:Date", "usage_count:Int"]
TXN_FIELDS = [
    "~from", "~to", "~label",
    "txn_id:String", "amount:Double", "currency:String", "type:String",
    "method:String", "location:String", "timestamp:Date", "status:String",
    "gen_type:String", "device_id:String",
]


def user_id(n: int) -> str:
    return f"User{n}"


def account_id(n: int) -> str:
    return f"Account{n}"


def device_id(n: int) -> str:
    return f"Device{n}"


def rng_for(seed: int, user_index: int) -> random.Random:
    return random.Random(seed ^ (user_index * 0x9E3779B97F4A7C15))


def iso_days_ago(rng: random.Random, days_min: int, days_max: int) -> str:
    days = rng.randint(days_min, days_max)
    hours = rng.randint(0, 23)
    minutes = rng.randint(0, 59)
    dt = datetime.utcnow() - timedelta(days=days, hours=hours, minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def account_balance(rng: random.Random, account_type: str) -> float:
    if account_type == "credit":
        return round(rng.uniform(-25_000, -500), 2)
    if account_type == "savings":
        return round(rng.uniform(5_000, 250_000), 2)
    return round(rng.uniform(500, 50_000), 2)


@dataclass
class ShardWriter:
    worker_id: int
    out_dir: Path
    users: csv.writer
    accounts: csv.writer
    devices: csv.writer
    owns: csv.writer
    uses: csv.writer
    transacts: csv.writer
    files: list

    @classmethod
    def open(cls, worker_id: int, out_dir: Path, write_headers: bool) -> ShardWriter:
        paths = {
            "users": out_dir / "vertices" / "users" / f"users_part_{worker_id:05d}.csv",
            "accounts": out_dir / "vertices" / "accounts" / f"accounts_part_{worker_id:05d}.csv",
            "devices": out_dir / "vertices" / "devices" / f"devices_part_{worker_id:05d}.csv",
            "owns": out_dir / "edges" / "ownership" / f"owns_part_{worker_id:05d}.csv",
            "uses": out_dir / "edges" / "usage" / f"uses_part_{worker_id:05d}.csv",
            "transacts": out_dir / "edges" / "transactions" / f"transacts_part_{worker_id:05d}.csv",
        }
        files = []
        writers = {}
        field_map = {
            "users": USER_FIELDS,
            "accounts": ACCOUNT_FIELDS,
            "devices": DEVICE_FIELDS,
            "owns": OWNS_FIELDS,
            "uses": USES_FIELDS,
            "transacts": TXN_FIELDS,
        }
        for key, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = path.open("w", newline="", encoding="utf-8", buffering=8 * 1024 * 1024)
            files.append(fh)
            w = csv.writer(fh)
            if write_headers:
                w.writerow(field_map[key])
            writers[key] = w
        return cls(worker_id, out_dir, writers["users"], writers["accounts"], writers["devices"],
                   writers["owns"], writers["uses"], writers["transacts"], files)

    def close(self) -> None:
        for fh in self.files:
            fh.close()


def generate_shard(args_tuple) -> dict:
    worker_id, start_idx, end_idx, num_users, seed, out_dir_str, currency, txns_per_user, fraud_rate = args_tuple
    out_dir = Path(out_dir_str)
    # Aerospike Graph bulk loader requires a schema header on EVERY CSV file,
    # not just the first shard. Always write headers.
    write_headers = True
    writer = ShardWriter.open(worker_id, out_dir, write_headers)

    users_written = 0
    txns_written = 0
    t0 = time.time()

    user_buf: list = []
    account_buf: list = []
    device_buf: list = []
    owns_buf: list = []
    uses_buf: list = []
    txn_buf: list = []

    def flush_bufs() -> None:
        nonlocal user_buf, account_buf, device_buf, owns_buf, uses_buf, txn_buf
        if user_buf:
            writer.users.writerows(user_buf)
            user_buf = []
        if account_buf:
            writer.accounts.writerows(account_buf)
            account_buf = []
        if device_buf:
            writer.devices.writerows(device_buf)
            device_buf = []
        if owns_buf:
            writer.owns.writerows(owns_buf)
            owns_buf = []
        if uses_buf:
            writer.uses.writerows(uses_buf)
            uses_buf = []
        if txn_buf:
            writer.transacts.writerows(txn_buf)
            txn_buf = []

    for i in range(start_idx, end_idx + 1):
        rng = rng_for(seed, i)
        uid = user_id(i)
        aid = account_id(i)
        did = device_id(i)

        # Optionally bake a fraud signal into a fraction of users so the review
        # queue and graph tools have something to surface in remote mode.
        is_fraud = fraud_rate > 0 and rng.random() < fraud_rate
        risk_score = round(rng.uniform(70.0, 95.0), 2) if is_fraud else 0.0

        signup_date = iso_days_ago(rng, 30, 730)
        user_buf.append([
            uid, "user", f"Demo User {i}", f"user{i}@demo.example",
            f"+1-555-{i % 10000:04d}", rng.randint(22, 65),
            LOCATIONS[i % len(LOCATIONS)], "Professional", risk_score, signup_date,
        ])

        acct_type = ACCOUNT_TYPES[i % len(ACCOUNT_TYPES)]
        # Fraud accounts skew newer (new-account risk) and carry the fraud flag.
        created_date = iso_days_ago(rng, 1, 25) if is_fraud else iso_days_ago(rng, 30, 900)
        account_buf.append([
            aid, "account", acct_type, account_balance(rng, acct_type),
            BANKS[i % len(BANKS)], "active", created_date, is_fraud,
        ])
        owns_buf.append([uid, aid, "OWNS", created_date])

        dev_type = DEVICE_TYPES[i % len(DEVICE_TYPES)]
        first_seen = iso_days_ago(rng, 7, 400)
        last_login = iso_days_ago(rng, 0, 14)
        login_count = rng.randint(10, 200)
        device_buf.append([
            did, "device", dev_type, rng.choice(DEVICE_OS[dev_type]),
            rng.choice(DEVICE_BROWSERS[dev_type]), f"fp-{i}",
            first_seen, last_login, login_count, is_fraud,
        ])
        uses_buf.append([uid, did, "USES", first_seen, last_login, login_count])

        # Fraud users fan out to many recipients with higher-value transfers.
        if is_fraud:
            txn_count = rng.randint(15, 40)
        else:
            txn_count = txns_per_user if txns_per_user > 0 else rng.randint(1, 5)
        for t in range(txn_count):
            receiver_idx = i
            while receiver_idx == i:
                receiver_idx = rng.randint(1, num_users)
            amount = round(rng.uniform(3_000, 25_000), 2) if is_fraud else round(rng.uniform(50, 15_000), 2)
            txn_buf.append([
                aid, account_id(receiver_idx), "TRANSACTS",
                f"txn-{i}-{t}", amount, currency,
                rng.choice(TXN_TYPES), rng.choice(TXN_METHODS), rng.choice(LOCATIONS),
                iso_days_ago(rng, 0, 30), "completed", "DEMO", did,
            ])
            txns_written += 1

        users_written += 1
        if users_written % BATCH_FLUSH == 0:
            flush_bufs()

    flush_bufs()
    writer.close()

    elapsed = time.time() - t0
    return {
        "worker_id": worker_id,
        "users": users_written,
        "transactions": txns_written,
        "elapsed_sec": round(elapsed, 2),
    }


def dir_size_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            total += (Path(root) / name).stat().st_size
    return total


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.2f} PB"


def plan_shards(num_users: int, workers: int) -> list[tuple[int, int, int]]:
    """Return list of (worker_id, start_idx, end_idx) covering [1, num_users]."""
    per = num_users // workers
    rem = num_users % workers
    shards = []
    cur = 1
    for w in range(workers):
        size = per + (1 if w < rem else 0)
        if size == 0:
            continue
        shards.append((w, cur, cur + size - 1))
        cur += size
    return shards


def main() -> None:
    parser = argparse.ArgumentParser(description="Parallel Aerospike Graph CSV generator")
    parser.add_argument("--users", type=int, required=True, help="Total users (e.g. 1000000000)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out_dir", type=str, default="./data/graph_csv")
    parser.add_argument("--txns-per-user", type=int, default=0,
                        help="Fixed txns per user (0 = random 1-5, default)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Parallel workers (default: CPU count)")
    parser.add_argument("--currency", type=str, default="USD")
    parser.add_argument("--fraud-rate", type=float, default=0.0,
                        help="Fraction of users (0..1) to bake a fraud signal into "
                             "(non-zero risk_score, fraud_flag, high-velocity txns). "
                             "Default 0 = clean dataset.")
    args = parser.parse_args()

    if args.users < 2:
        parser.error("--users must be >= 2")
    if not (0.0 <= args.fraud_rate <= 1.0):
        parser.error("--fraud-rate must be between 0 and 1")

    workers = args.workers or multiprocessing.cpu_count()
    out_dir = Path(args.out_dir)
    shards = plan_shards(args.users, workers)

    txn_mode = f"{args.txns_per_user} fixed" if args.txns_per_user > 0 else "1-5 random"
    print(f"Generating {args.users:,} users + transactions")
    print(f"  workers: {workers} shards")
    print(f"  txns/user: {txn_mode}")
    print(f"  fraud-rate: {args.fraud_rate:.4f}")
    print(f"  output:  {out_dir.resolve()}")

    t0 = time.time()
    tasks = [
        (wid, start, end, args.users, args.seed, str(out_dir.resolve()), args.currency,
         args.txns_per_user, args.fraud_rate)
        for wid, start, end in shards
    ]

    if workers == 1:
        results = [generate_shard(tasks[0])]
    else:
        with multiprocessing.Pool(processes=len(shards)) as pool:
            results = pool.map(generate_shard, tasks)

    elapsed = time.time() - t0
    total_users = sum(r["users"] for r in results)
    total_txns = sum(r["transactions"] for r in results)
    size = dir_size_bytes(out_dir)

    print(f"\nDone in {elapsed:.1f}s")
    print(f"  users written:        {total_users:,}")
    print(f"  transactions written: {total_txns:,}")
    print(f"  avg txns/user:        {total_txns / total_users:.2f}")
    print(f"  total on disk:        {human_bytes(size)}")
    print(f"  bytes/user (all CSV): {size / total_users:.1f}")
    if args.users != total_users:
        print(f"  WARNING: expected {args.users:,} users, got {total_users:,}", file=sys.stderr)


if __name__ == "__main__":
    main()
