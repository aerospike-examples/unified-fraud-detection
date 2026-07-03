package com.aerospike.fraudload;

import org.junit.jupiter.api.Test;
import java.nio.file.Path;
import java.util.Random;
import static org.junit.jupiter.api.Assertions.*;

class TransactionGeneratorTest {
    @Test
    void generatesValidDistinctEndpoints() {
        AccountPool pool = AccountPool.synthetic(100);
        TransactionGenerator gen = new TransactionGenerator(pool, new Random(42));
        for (int i = 0; i < 1000; i++) {
            Transaction t = gen.next();
            assertNotNull(t.txnId());
            assertNotEquals(t.senderAccountId(), t.receiverAccountId(), "sender must differ from receiver");
            assertTrue(t.amountCents() > 0);
            assertTrue(t.senderAccountId().startsWith("acct-"));
        }
    }

    @Test
    void respectsShardBounds() {
        AccountPool pool = AccountPool.synthetic(100);
        KeyShard shard = new KeyShard(0, 4, 100);
        TransactionGenerator gen = new TransactionGenerator(pool, shard, new Random(7));
        for (int i = 0; i < 500; i++) {
            Transaction t = gen.next();
            int senderIdx = Integer.parseInt(t.senderAccountId().substring(5));
            int receiverIdx = Integer.parseInt(t.receiverAccountId().substring(5));
            assertTrue(senderIdx >= shard.firstIndex() && senderIdx < shard.endIndex());
            assertTrue(receiverIdx >= shard.firstIndex() && receiverIdx < shard.endIndex());
        }
    }
}
