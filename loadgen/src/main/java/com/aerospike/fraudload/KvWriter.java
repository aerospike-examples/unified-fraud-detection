package com.aerospike.fraudload;

import com.aerospike.client.AerospikeClient;
import com.aerospike.client.Bin;
import com.aerospike.client.Key;
import com.aerospike.client.Operation;
import com.aerospike.client.Value;
import com.aerospike.client.cdt.MapOperation;
import com.aerospike.client.cdt.MapOrder;
import com.aerospike.client.cdt.MapPolicy;
import com.aerospike.client.cdt.MapWriteFlags;
import com.aerospike.client.policy.WritePolicy;

import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Map;

/** Writes a transaction into KV using a single atomic map-put per side (no read-modify-write). */
public final class KvWriter {
    private static final DateTimeFormatter HOUR_FMT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd-HH").withZone(ZoneOffset.UTC);
    private static final DateTimeFormatter DAY_FMT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ZoneOffset.UTC);

    private final AerospikeClient client;
    private final String namespace;
    private final boolean updateBalances;
    private final KvModel model;
    private final String txnSet;
    private final WritePolicy writePolicy;
    private final com.aerospike.client.policy.Policy readPolicy;
    private final MapPolicy mapPolicy;

    public KvWriter(AerospikeClient client, String namespace, boolean updateBalances, KvModel model,
                    int ttlSeconds) {
        this.client = client;
        this.namespace = namespace;
        this.updateBalances = updateBalances;
        this.model = model;
        this.txnSet = model.setName();
        this.readPolicy = new com.aerospike.client.policy.Policy(client.readPolicyDefault);
        this.writePolicy = new WritePolicy(client.writePolicyDefault);
        // A flat-model run mints a brand new record per transaction, so at
        // millions of txn/s it will happily fill the namespace and trip
        // stop-writes. A TTL keeps benchmark data self-cleaning.
        if (ttlSeconds > 0) {
            this.writePolicy.expiration = ttlSeconds;
        }
        this.mapPolicy = new MapPolicy(MapOrder.UNORDERED, MapWriteFlags.DEFAULT);
    }

    String recordKey(String accountId, java.time.Instant ts) {
        return accountId + ":" + HOUR_FMT.format(ts);
    }

    private void writeSide(Transaction t, String accountId, String direction, String counterparty) {
        String rk = recordKey(accountId, t.timestamp());
        Key key = new Key(namespace, txnSet, rk);
        Map<String, Object> entry = t.toEntry(direction, counterparty);
        String tsKey = t.timestamp().toString();
        client.operate(writePolicy, key,
                MapOperation.put(mapPolicy, "txs", Value.get(tsKey), Value.get(entry)),
                Operation.put(new Bin("account_id", accountId)),
                Operation.put(new Bin("day", DAY_FMT.format(t.timestamp()))));

        if (updateBalances) {
            long delta = direction.equals("out") ? -t.amountCents() : t.amountCents();
            applyBalance(accountId, delta);
        }
    }

    /** One constant-size record for the whole transaction — no CDT, no per-side duplicate. */
    private void writeFlat(Transaction t) {
        Key key = new Key(namespace, txnSet, t.txnId());
        client.put(writePolicy, key,
                new Bin("sender", t.senderAccountId()),
                new Bin("receiver", t.receiverAccountId()),
                new Bin("amount", t.amountCents() / 100.0),
                new Bin("type", t.type()),
                new Bin("location", t.location()),
                new Bin("ts", t.timestamp().toString()),
                new Bin("day", DAY_FMT.format(t.timestamp())),
                new Bin("is_fraud", Value.get(t.fraud())),
                new Bin("fraud_score", t.fraudScore()));

        if (updateBalances) {
            applyBalance(t.senderAccountId(), -t.amountCents());
            applyBalance(t.receiverAccountId(), t.amountCents());
        }
    }

    private void applyBalance(String accountId, long deltaCents) {
        Key balKey = new Key(namespace, "account_balance", accountId);
        client.operate(writePolicy, balKey, Operation.add(new Bin("bal", deltaCents)));
    }

    /** Primary key this transaction lands under, for reading it back later. */
    String primaryKey(Transaction t) {
        return model == KvModel.flat ? t.txnId() : recordKey(t.senderAccountId(), t.timestamp());
    }

    /** Single-record read of a previously written transaction record. */
    public void readTransaction(String primaryKey) {
        client.get(readPolicy, new Key(namespace, txnSet, primaryKey));
    }

    public void writeTransaction(Transaction t) {
        if (model == KvModel.flat) {
            writeFlat(t);
            return;
        }
        writeSide(t, t.senderAccountId(), "out", t.receiverAccountId());
        writeSide(t, t.receiverAccountId(), "in", t.senderAccountId());
    }
}
