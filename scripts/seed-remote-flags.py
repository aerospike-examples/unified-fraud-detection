#!/usr/bin/env python3
"""
Seed a review queue for remote (externally bulk-loaded) mode.

The bulk-load CSVs contain no fraud signal (every user's risk_score is 0.0 and
every account/device fraud_flag is False), and in remote mode the app does not
run detection. So nothing populates the KV `flagged_accounts` set and the review
queue is empty. This script closes that gap for a demo without a full pipeline:

  1. Selects a bounded set of users (by index range, or an explicit list).
  2. Injects fraud signal into the graph for them: raises the user vertex
     risk_score and sets fraud_flag on their accounts/devices.
  3. (optional) Wires a fraud ring: shared device + inter-account TRANSACTS
     edges so detect_fraud_ring / get_transaction_network light up.
  4. Computes bounded account facts from the graph, scores them with the same ML
     model the app uses, and writes one KV `flagged_accounts` record per user.

Everything is bounded per user; there are no full-graph scans, so it is safe to
run against a billion-vertex graph.

Usage:
  python scripts/seed-remote-flags.py --count 50
  python scripts/seed-remote-flags.py --users User10,User11,User12 --ring
  python scripts/seed-remote-flags.py --count 100 --ring --velocity --hydrate
  python scripts/seed-remote-flags.py --count 100 --start 1 --ring --ring-size 5 --velocity --velocity-count 30

Connection comes from the same env the backend uses (GRAPH_HOST_ADDRESS,
AEROSPIKE_HOST, ...). Run it from anywhere; it adds ./backend to sys.path.
"""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta

# Make backend importable regardless of CWD.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

from gremlin_python.process.graph_traversal import __  # noqa: E402

from services.graph_service import GraphService  # noqa: E402
from services.aerospike_service import aerospike_service  # noqa: E402
from services.ml_service import ml_model_service  # noqa: E402
from services.hydration_service import HydrationService  # noqa: E402

FLAG_REASONS = [
    "High transaction velocity",
    "Shared device with flagged accounts",
    "Fan-out to many new recipients",
    "Reciprocal money flow (round-tripping)",
    "High-value transfers from new account",
    "Part of a dense transaction cluster",
]


def parse_args():
    p = argparse.ArgumentParser(description="Seed remote-mode review queue with fraud signal.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--count", type=int, default=50, help="Number of users to flag (from --start).")
    g.add_argument("--users", type=str, help="Explicit comma-separated user IDs (e.g. User10,User11).")
    p.add_argument("--start", type=int, default=1, help="First user index when using --count (default 1).")
    p.add_argument("--prefix", type=str, default="User", help="User ID prefix (default 'User').")
    p.add_argument("--ring", action="store_true", help="Also wire fraud-ring structure among flagged users.")
    p.add_argument("--ring-size", type=int, default=5, help="Users per ring (default 5).")
    p.add_argument("--velocity", action="store_true",
                   help="Inject high-value fan-out TRANSACTS edges per flagged user so computed "
                        "features (velocity/amount/entropy) look genuinely fraudulent.")
    p.add_argument("--velocity-count", type=int, default=25,
                   help="Fan-out edges to add per flagged user when --velocity is set (default 25).")
    p.add_argument("--hydrate", action="store_true", help="Pre-hydrate the KV working set for each user.")
    p.add_argument("--min-risk", type=float, default=68.0, help="Minimum injected user risk_score.")
    p.add_argument("--max-risk", type=float, default=95.0, help="Maximum injected user risk_score.")
    p.add_argument("--max-edges", type=int, default=2000, help="Per-account traversal cap.")
    p.add_argument("--dry-run", action="store_true", help="Compute and log but do not write anything.")
    return p.parse_args()


def resolve_user_ids(args) -> list:
    if args.users:
        return [u.strip() for u in args.users.split(",") if u.strip()]
    return [f"{args.prefix}{i}" for i in range(args.start, args.start + args.count)]


def get_accounts(g, user_id, cap):
    try:
        return g.V(user_id).out("OWNS").id_().limit(cap).to_list()
    except Exception:
        return []


def get_devices(g, user_id, cap):
    try:
        return g.V(user_id).out("USES").id_().limit(cap).to_list()
    except Exception:
        return []


def inject_ring(g, ring_users, rng, dry_run):
    """Give a group of users a shared device and inter-connect their accounts."""
    if len(ring_users) < 2:
        return
    # Collect one account per member and use the first member's device as shared.
    member_accounts = []
    for uid in ring_users:
        accts = get_accounts(g, uid, 1)
        if accts:
            member_accounts.append(accts[0])
    devices = get_devices(g, ring_users[0], 1)
    shared_device = devices[0] if devices else None

    if dry_run:
        print(f"    [dry-run] ring of {len(ring_users)} users, shared_device={shared_device}, "
              f"{len(member_accounts)} accounts to interlink")
        return

    # Everyone USES the shared device (device+transaction overlap = strong signal).
    if shared_device:
        for uid in ring_users[1:]:
            try:
                exists = g.V(uid).out("USES").hasId(shared_device).has_next()
                if not exists:
                    g.V(uid).addE("USES").to(__.V(shared_device)) \
                        .property("first_used", datetime.now().isoformat()) \
                        .property("last_used", datetime.now().isoformat()) \
                        .property("usage_count", rng.randint(5, 50)).iterate()
            except Exception as e:
                print(f"    ring USES edge {uid}->{shared_device} failed: {e}")
        try:
            g.V(shared_device).property("fraud_flag", True).iterate()
        except Exception:
            pass

    # Cycle of TRANSACTS edges among member accounts.
    n = len(member_accounts)
    for idx in range(n):
        a_from = member_accounts[idx]
        a_to = member_accounts[(idx + 1) % n]
        if a_from == a_to:
            continue
        try:
            g.V(a_from).addE("TRANSACTS").to(__.V(a_to)) \
                .property("txn_id", f"seed-ring-{a_from}-{a_to}") \
                .property("amount", round(rng.uniform(2000, 12000), 2)) \
                .property("currency", "USD") \
                .property("type", "transfer") \
                .property("timestamp", datetime.now().isoformat()) \
                .property("status", "completed") \
                .property("gen_type", "seed_ring").iterate()
        except Exception as e:
            print(f"    ring TXN edge {a_from}->{a_to} failed: {e}")


def inject_velocity(g, sender_account, receiver_pool, count, rng, dry_run):
    """Add `count` high-value fan-out TRANSACTS edges from sender_account to
    distinct accounts in receiver_pool, creating real velocity/amount/entropy
    signal for the flagged account. Returns edges added."""
    receivers = [a for a in receiver_pool if a and a != sender_account]
    if not receivers:
        return 0
    n = min(count, len(receivers))
    chosen = rng.sample(receivers, n)
    if dry_run:
        return n
    added = 0
    for r in chosen:
        try:
            g.V(sender_account).addE("TRANSACTS").to(__.V(r)) \
                .property("txn_id", f"seed-vel-{sender_account}-{r}") \
                .property("amount", round(rng.uniform(3000, 25000), 2)) \
                .property("currency", "USD") \
                .property("type", "transfer") \
                .property("method", "electronic_transfer") \
                .property("location", "") \
                .property("timestamp", datetime.now().isoformat()) \
                .property("status", "completed") \
                .property("gen_type", "seed_velocity") \
                .property("device_id", "").iterate()
            added += 1
        except Exception as e:
            print(f"    velocity edge {sender_account}->{r} failed: {e}")
    return added


def main():
    args = parse_args()
    rng = random.Random(1234)

    graph_service = GraphService()
    try:
        graph_service.connect()
    except Exception as e:
        print(f"ERROR: could not connect to the graph (check GRAPH_HOST_ADDRESS): {e}")
        sys.exit(1)
    if not graph_service.client:
        print("ERROR: graph client unavailable after connect.")
        sys.exit(1)
    g = graph_service.client

    try:
        connected = aerospike_service.connect()
    except Exception as e:
        connected = False
        print(f"ERROR connecting to Aerospike KV: {e}")
    if not connected:
        print("ERROR: could not connect to Aerospike KV. Check AEROSPIKE_HOST.")
        sys.exit(1)

    user_ids = resolve_user_ids(args)
    print(f"Seeding {len(user_ids)} users "
          f"({'dry-run' if args.dry_run else 'writing'}), ring={args.ring}, velocity={args.velocity}")

    # Build a receiver pool (one account per selected user) for velocity fan-out.
    receiver_pool = []
    if args.velocity:
        for uid in user_ids:
            accts = get_accounts(g, uid, 1)
            if accts:
                receiver_pool.append(accts[0])

    # Optionally build rings first so the injected edges are reflected in facts.
    if args.ring:
        for i in range(0, len(user_ids), args.ring_size):
            ring = user_ids[i:i + args.ring_size]
            print(f"  Ring {i // args.ring_size + 1}: {ring}")
            inject_ring(g, ring, rng, args.dry_run)

    flagged = 0
    missing = 0
    for uid in user_ids:
        profile = graph_service.get_user_profile(uid)
        if not profile or not profile.get("user"):
            missing += 1
            print(f"  SKIP {uid}: no graph vertex")
            continue

        risk = round(rng.uniform(args.min_risk, args.max_risk), 2)
        accounts = get_accounts(g, uid, 25)
        devices = get_devices(g, uid, 25)

        if not args.dry_run:
            try:
                g.V(uid).property("risk_score", risk).iterate()
                for aid in accounts:
                    g.V(aid).property("fraud_flag", True).iterate()
                for did in devices:
                    g.V(did).property("fraud_flag", True).iterate()
            except Exception as e:
                print(f"  {uid}: graph property writes failed: {e}")

        # Inject velocity BEFORE computing facts so the facts reflect it.
        if args.velocity and accounts:
            added = inject_velocity(g, accounts[0], receiver_pool, args.velocity_count, rng, args.dry_run)
            if added:
                print(f"  {uid}: +{added} fan-out txns on {accounts[0]}")

        # Score accounts from the graph so the flagged record has real predictions.
        account_predictions = []
        max_acct_risk = 0.0
        for aid in accounts:
            fact = graph_service.compute_account_fact_from_graph(aid, max_edges=args.max_edges)
            try:
                acct_risk = ml_model_service.predict_account_risk(fact).get("risk_score", 0)
            except Exception:
                acct_risk = 0
            account_predictions.append({"account_id": aid, "risk_score": acct_risk})
            max_acct_risk = max(max_acct_risk, acct_risk)

        # Final user risk = max of injected floor and computed account risk.
        user_risk = max(risk, max_acct_risk)
        u = profile["user"]
        top_account = max(account_predictions, key=lambda p: p["risk_score"], default={"account_id": uid})

        flagged_record = {
            "account_id": top_account["account_id"],
            "user_id": uid,
            "account_holder": u.get("name", f"User {uid}"),
            "email": u.get("email", ""),
            "risk_score": round(user_risk, 2),
            "status": "pending_review",
            "flag_reason": rng.choice(FLAG_REASONS),
            "reason": rng.choice(FLAG_REASONS),
            "flagged_date": (datetime.now() - timedelta(hours=rng.randint(0, 72))).isoformat(),
            "total_flagged_amount": round(rng.uniform(5000, 150000), 2),
            "account_predictions": account_predictions,
            "model_version": "seed-remote-v1",
            "confidence": round(rng.uniform(0.75, 0.95), 2),
            "source": "seed_script",
            "created_at": datetime.now().isoformat(),
        }

        if args.dry_run:
            print(f"  {uid}: risk={flagged_record['risk_score']} "
                  f"accounts={len(accounts)} devices={len(devices)}")
        else:
            if aerospike_service.flag_account(flagged_record):
                flagged += 1
            if args.hydrate:
                try:
                    HydrationService(graph_service, aerospike_service).ensure_user_hydrated(uid)
                except Exception as e:
                    print(f"  {uid}: hydrate failed: {e}")

    print(f"\nDone. flagged={flagged}, missing={missing}, requested={len(user_ids)}")
    if not args.dry_run:
        print("Review queue is now populated. Open the app (remote mode) to investigate.")


if __name__ == "__main__":
    main()
