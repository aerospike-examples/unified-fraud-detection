package com.aerospike.fraudload;

import org.junit.jupiter.api.Test;
import java.nio.file.Path;
import static org.junit.jupiter.api.Assertions.*;

class AccountPoolTest {
    @Test
    void loadsAccountsFromCsv() throws Exception {
        Path csv = Path.of("../data/graph_csv/vertices/accounts/accounts.csv");
        if (!csv.toFile().isFile()) {
            csv = Path.of("data/graph_csv/vertices/accounts/accounts.csv");
        }
        assumeFileExists(csv);
        AccountPool pool = AccountPool.fromCsv(csv, 10);
        assertEquals(10, pool.size());
        assertTrue(pool.idAt(0).startsWith("A"));
    }

    @Test
    void deterministicPoolMatchesBulkLoadIds() {
        AccountPool pool = AccountPool.deterministic(5, "Account");
        assertEquals(5, pool.size());
        assertEquals("Account1", pool.idAt(0));
        assertEquals("Account5", pool.idAt(4));
    }

    @Test
    void syntheticPoolHasExpectedIds() {
        AccountPool pool = AccountPool.synthetic(5);
        assertEquals(5, pool.size());
        assertEquals("acct-0", pool.idAt(0));
        assertEquals("acct-4", pool.idAt(4));
    }

    private static void assumeFileExists(Path path) {
        org.junit.jupiter.api.Assumptions.assumeTrue(path.toFile().isFile(),
                "accounts CSV not found at " + path);
    }
}
