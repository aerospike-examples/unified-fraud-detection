package com.aerospike.fraudload;

/** How a transaction is laid out in KV. */
public enum KvModel {
    /**
     * Two records per transaction (one per side), keyed {@code account:hour},
     * each holding a growing {@code txs} CDT map. This is what the demo UI's
     * account-scoped reads expect, but every write is a server-side
     * read-modify-write of a record that grows for as long as the account keeps
     * transacting inside the hour, so sustained throughput degrades as a run
     * progresses (worse the smaller the account pool).
     */
    bucketed,
    /**
     * One fixed-size record per transaction, keyed by txn id, with plain bins
     * and no CDT. Record size never grows, so throughput stays flat for the
     * whole run — use this to measure what Aerospike can actually absorb.
     * Written to its own set, and not readable by the demo's account-scoped
     * queries.
     */
    flat;

    /** Set name transactions land in under this model. */
    public String setName() {
        return this == flat ? "transactions_flat" : "transactions";
    }
}
