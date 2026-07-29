package com.aerospike.fraudload;

import com.aerospike.client.AerospikeClient;
import com.aerospike.client.Key;
import com.aerospike.client.Record;
import com.aerospike.client.AerospikeException;
import org.junit.jupiter.api.Test;
import java.time.Instant;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

class KvWriterIT {
    private AerospikeClient connectOrSkip() {
        try {
            return new AerospikeClient("localhost", 3000);
        } catch (AerospikeException e) {
            assumeTrue(false, "Aerospike not reachable on localhost:3000 — skipping IT");
            return null;
        }
    }

    @Test
    void writesMapEntryAndIncrementsBalance() {
        AerospikeClient client = connectOrSkip();
        try {
            KvWriter writer = new KvWriter(client, "test", true);
            Instant ts = Instant.parse("2026-06-30T12:00:00Z");
            Transaction t = new Transaction("txn-it-1", "acct-1", "acct-2",
                    250_00L, "transfer", "Austin, TX", ts);
            writer.writeTransaction(t);

            Key senderDay = new Key("test", "transactions", writer.recordKey("acct-1", ts));
            Record rec = client.get(null, senderDay);
            assertNotNull(rec, "sender day record should exist");
            @SuppressWarnings("unchecked")
            Map<Object, Object> txs = (Map<Object, Object>) rec.getMap("txs");
            assertTrue(txs.containsKey(ts.toString()), "txs map should contain the timestamp entry");

            Record bal = client.get(null, new Key("test", "account_balance", "acct-1"));
            assertNotNull(bal);
            assertTrue(bal.getLong("bal") <= -250_00L, "sender balance should be debited");
        } finally {
            if (client != null) client.close();
        }
    }
}
