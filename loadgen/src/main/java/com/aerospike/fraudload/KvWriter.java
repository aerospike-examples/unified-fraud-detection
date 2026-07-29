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
    private final WritePolicy writePolicy;
    private final MapPolicy mapPolicy;

    public KvWriter(AerospikeClient client, String namespace, boolean updateBalances) {
        this.client = client;
        this.namespace = namespace;
        this.updateBalances = updateBalances;
        this.writePolicy = new WritePolicy(client.writePolicyDefault);
        this.mapPolicy = new MapPolicy(MapOrder.UNORDERED, MapWriteFlags.DEFAULT);
    }

    String recordKey(String accountId, java.time.Instant ts) {
        return accountId + ":" + HOUR_FMT.format(ts);
    }

    private void writeSide(Transaction t, String accountId, String direction, String counterparty) {
        String rk = recordKey(accountId, t.timestamp());
        Key key = new Key(namespace, "transactions", rk);
        Map<String, Object> entry = t.toEntry(direction, counterparty);
        String tsKey = t.timestamp().toString();
        client.operate(writePolicy, key,
                MapOperation.put(mapPolicy, "txs", Value.get(tsKey), Value.get(entry)),
                Operation.put(new Bin("account_id", accountId)),
                Operation.put(new Bin("day", DAY_FMT.format(t.timestamp()))));

        if (updateBalances) {
            long delta = direction.equals("out") ? -t.amountCents() : t.amountCents();
            Key balKey = new Key(namespace, "account_balance", accountId);
            client.operate(writePolicy, balKey, Operation.add(new Bin("bal", delta)));
        }
    }

    public void writeTransaction(Transaction t) {
        writeSide(t, t.senderAccountId(), "out", t.receiverAccountId());
        writeSide(t, t.receiverAccountId(), "in", t.senderAccountId());
    }
}
