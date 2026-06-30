package com.aerospike.fraudload;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class KeyShardTest {
    @Test
    void shardsAreDisjointAndCoverWholeSpace() {
        int accountCount = 1000;
        int workers = 4;
        boolean[] seen = new boolean[accountCount];
        int total = 0;
        for (int w = 0; w < workers; w++) {
            KeyShard shard = new KeyShard(w, workers, accountCount);
            for (int id = shard.firstIndex(); id < shard.endIndex(); id++) {
                assertFalse(seen[id], "index assigned to two shards: " + id);
                seen[id] = true;
                total++;
            }
        }
        assertEquals(accountCount, total);
        for (boolean b : seen) assertTrue(b);
    }
}
