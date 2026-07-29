#!/usr/bin/env python3
"""
Retrofit fraud-ring graph structure onto accounts that are ALREADY flagged.

Context: live fraud injection (loadgen) picks a fresh random sender/receiver
for every fraudulent transaction — no fixed cohort — so it never builds the
kind of repeated multi-account structure (transaction triangles, reciprocal
flows, dense clusters) that detect_fraud_ring's structural analysis looks
for. That's fixed going forward by biasing a fraction of new fraud txns
toward a small rotating "ring pool" per loadgen worker (see
TransactionGenerator.fraudTransaction), but it does nothing for the accounts
already sitting in the review queue from prior runs.

This script closes that gap directly against the graph, with no loadgen run
needed: for each already-flagged account (the "seed"), it picks several OTHER
accounts from anywhere in the pool — not necessarily pre-flagged — and wires
a small dense cluster of TRANSACTS edges among all of them. Investigating the
seed afterward will have detect_fraud_ring surface the other cluster members
as ring_members, even though nothing flagged them independently.

Bounded per cluster; safe to run against a billion-vertex graph.

Usage:
  python scripts/backfill-fraud-rings.py --count 500
  python scripts/backfill-fraud-rings.py --count 50 --dry-run
  python scripts/backfill-fraud-rings.py --count 2000 --cluster-size 15 --edge-density 2.0

Connection comes from the same env the backend uses (GRAPH_HOST_ADDRESS,
AEROSPIKE_HOST, ...). Run it from anywhere; it adds ./backend to sys.path.
"""

import argparse
import os
import random
import sys
import uuid
from datetime import datetime, timedelta

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

from gremlin_python.process.graph_traversal import __  # noqa: E402

from services.graph_service import GraphService  # noqa: E402
from services.aerospike_service import aerospike_service  # noqa: E402

TYPES = ["transfer", "payment", "purchase", "withdrawal", "deposit"]
LOCATIONS = ["New York, NY", "Chicago, IL", "Austin, TX", "Seattle, WA"]


def parse_args():
    p = argparse.ArgumentParser(description="Backfill fraud-ring structure onto already-flagged accounts.")
    p.add_argument("--count", type=int, default=500,
                   help="Number of already-flagged accounts to use as ring seeds (default 500).")
    p.add_argument("--cluster-size", type=int, default=12,
                   help="Accounts per ring cluster, including the seed (default 12).")
    p.add_argument("--inter-pair-fraction", type=float, default=0.45,
                   help="Fraction of all (non-seed) member pairs that also transact directly with each "
                        "other, on top of full seed<->member reciprocity (default 0.45). This is what "
                        "detect_fraud_ring's triangle/cluster-density scoring keys off of.")
    p.add_argument("--min-amount", type=float, default=3_000.0, help="Min synthetic edge amount.")
    p.add_argument("--max-amount", type=float, default=50_000.0, help="Max synthetic edge amount.")
    p.add_argument("--min-fraud-score", type=float, default=70.0)
    p.add_argument("--max-fraud-score", type=float, default=99.0)
    p.add_argument("--account-prefix", type=str, default="Account")
    p.add_argument("--rng-seed", type=int, default=20260715, help="RNG seed (default fixed for reproducibility).")
    p.add_argument("--dry-run", action="store_true", help="Compute clusters but do not write edges.")
    return p.parse_args()


def fetch_seed_accounts(count, rng):
    """Pull already-flagged accounts as ring seeds — no flagged_accounts scan.

    Reads the sharded flagged_queue index (one cheap batch-get across a fixed
    number of shard keys, regardless of how many accounts are flagged), then
    point-reads (batch_get by user_id) just the sampled subset. Cost is
    ~constant in queue size instead of scanning the whole set."""
    user_ids = aerospike_service.get_flagged_queue_user_ids()
    if not user_ids:
        return []
    sample_ids = rng.sample(user_ids, min(count, len(user_ids)))
    records = aerospike_service.get_flagged_accounts_batch(sample_ids)
    seeds = []
    for r in records:
        account_id = r.get("account_id")
        user_id = r.get("user_id")
        if account_id and user_id:
            seeds.append((account_id, user_id))
    return seeds


def total_account_count(graph_service):
    summary = graph_service.get_graph_summary()
    total = (summary.get("vertex_counts") or {}).get("account", 0)
    if not total:
        raise RuntimeError("could not read account count from graph summary")
    return int(total)


def build_cluster_edges(seed_account, other_members, inter_pair_fraction, rng):
    """Returns a deduped list of directed (from, to) edges wiring seed_account
    to every member of other_members, plus a share of edges among the other
    members themselves.

    detect_fraud_ring scores almost entirely off two structural signals: (1)
    reciprocal partners — members with money flowing BOTH ways vs the seed —
    3+ scores its strongest bonus; and (2) cluster density — what fraction of
    the seed's partners also transact with EACH OTHER (not just with the
    seed), which is what makes something a "triangle". A simple star (seed to
    N independent members) or sparse random edges satisfies neither well. So:
      - every other_member gets a bidirectional edge with the seed, which
        guarantees full reciprocity (reciprocal_partner_count == len(other_members));
      - `inter_pair_fraction` of all (member, member) pairs — excluding the
        seed — also get a direct edge, which is what turns partners into a
        dense, triangle-rich cluster instead of an disconnected star.
    """
    edges = []
    seen_directed = set()

    def add_edge(a, b):
        if a == b or (a, b) in seen_directed:
            return False
        seen_directed.add((a, b))
        edges.append((a, b))
        return True

    for m in other_members:
        add_edge(seed_account, m)
        add_edge(m, seed_account)

    other_pairs = [(a, b) for i, a in enumerate(other_members) for b in other_members[i + 1:]]
    rng.shuffle(other_pairs)
    n_inter = round(len(other_pairs) * inter_pair_fraction)
    for a, b in other_pairs[:n_inter]:
        if rng.random() < 0.5:
            add_edge(a, b)
        else:
            add_edge(b, a)

    return edges


def write_cluster(g, edges, rng, args, dry_run):
    now = datetime.now()
    written = 0
    for (a, b) in edges:
        if dry_run:
            written += 1
            continue
        try:
            g.V(a).addE("TRANSACTS").to(__.V(b)) \
                .property("txn_id", f"backfill-ring-{uuid.uuid4()}") \
                .property("amount", round(rng.uniform(args.min_amount, args.max_amount), 2)) \
                .property("currency", "USD") \
                .property("type", rng.choice(TYPES)) \
                .property("method", "electronic_transfer") \
                .property("location", rng.choice(LOCATIONS)) \
                .property("timestamp", (now - timedelta(minutes=rng.randint(0, 4320))).isoformat()) \
                .property("status", "completed") \
                .property("is_fraud", True) \
                .property("fraud_score", round(rng.uniform(args.min_fraud_score, args.max_fraud_score), 2)) \
                .property("gen_type", "backfill_ring") \
                .iterate()
            written += 1
        except Exception as e:
            print(f"    edge {a}->{b} failed: {e}")
    return written


def main():
    args = parse_args()
    rng = random.Random(args.rng_seed)

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

    seeds = fetch_seed_accounts(args.count, rng)
    if not seeds:
        print("ERROR: no flagged accounts found in flagged_queue — nothing to backfill.")
        sys.exit(1)

    total_accounts = total_account_count(graph_service)
    print(f"Backfilling {len(seeds)} ring(s) (cluster_size={args.cluster_size}, "
          f"inter_pair_fraction={args.inter_pair_fraction}, account_pool={total_accounts:,}), "
          f"{'dry-run' if args.dry_run else 'writing'}")

    used_account_ids = {a for a, _ in seeds}
    total_edges = 0
    clusters_written = 0
    for i, (seed_account, seed_user) in enumerate(seeds):
        others = set()
        guard = 0
        while len(others) < args.cluster_size - 1 and guard < (args.cluster_size - 1) * 20:
            guard += 1
            candidate = f"{args.account_prefix}{rng.randint(1, total_accounts)}"
            if candidate != seed_account and candidate not in used_account_ids:
                others.add(candidate)
        others = list(others)
        if len(others) < 2:
            print(f"  [{i + 1}/{len(seeds)}] {seed_account}: could not find enough distinct members, skipping")
            continue

        edges = build_cluster_edges(seed_account, others, args.inter_pair_fraction, rng)
        written = write_cluster(g, edges, rng, args, args.dry_run)
        total_edges += written
        clusters_written += 1
        print(f"  [{i + 1}/{len(seeds)}] {seed_account} (user={seed_user}): "
              f"{len(others) + 1} members, {written}/{len(edges)} edges written")

    print(f"\nDone. clusters={clusters_written}, edges={total_edges} "
          f"({'dry-run, nothing written' if args.dry_run else 'written to graph'})")
    if not args.dry_run:
        print("Open any of the seed accounts in the review queue and re-investigate — "
              "detect_fraud_ring should now surface the ring.")


if __name__ == "__main__":
    main()
