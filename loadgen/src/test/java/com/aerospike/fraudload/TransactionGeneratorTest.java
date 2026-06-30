package com.aerospike.fraudload;

import org.junit.jupiter.api.Test;
import java.util.Random;
import static org.junit.jupiter.api.Assertions.*;

class TransactionGeneratorTest {
    @Test
    void generatesValidDistinctEndpoints() {
        TransactionGenerator gen = new TransactionGenerator(100, new Random(42));
        for (int i = 0; i < 1000; i++) {
            Transaction t = gen.next();
            assertNotNull(t.txnId());
            assertNotEquals(t.senderAccountId(), t.receiverAccountId(), "sender must differ from receiver");
            assertTrue(t.amountCents() > 0);
            assertTrue(t.senderAccountId().startsWith("acct-"));
        }
    }
}
