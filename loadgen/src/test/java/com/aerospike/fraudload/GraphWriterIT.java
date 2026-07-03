package com.aerospike.fraudload;

import org.junit.jupiter.api.Test;
import java.nio.file.Path;
import java.time.Instant;
import static org.junit.jupiter.api.Assumptions.assumeTrue;
import static org.junit.jupiter.api.Assertions.*;

class GraphWriterIT {
    private static final Path ACCOUNTS_CSV = resolveAccountsCsv();

    @Test
    void writesTransactsEdge() throws Exception {
        assumeTrue(accountsCsvExists(), "accounts CSV not found — skipping");
        assumeGremlinReachable();
        AccountPool pool = AccountPool.fromCsv(ACCOUNTS_CSV, 100);
        Instant ts = Instant.parse("2026-06-30T12:00:00Z");
        Transaction t = new Transaction(
                "txn-graph-it-1",
                pool.idAt(0),
                pool.idAt(1),
                100_00L,
                "transfer",
                "Austin, TX",
                ts);
        try (GraphWriter writer = new GraphWriter("localhost", 8182, 4)) {
            assertDoesNotThrow(() -> writer.writeEdge(t));
        }
    }

    private static void assumeGremlinReachable() {
        try (GraphWriter writer = new GraphWriter("localhost", 8182, 2)) {
            // connection opened successfully
        } catch (Exception e) {
            assumeTrue(false, "Gremlin not reachable on localhost:8182 — skipping");
        }
    }

    private static Path resolveAccountsCsv() {
        Path p = Path.of("../data/graph_csv/vertices/accounts/accounts.csv");
        if (p.toFile().isFile()) return p;
        return Path.of("data/graph_csv/vertices/accounts/accounts.csv");
    }

    private static boolean accountsCsvExists() {
        return ACCOUNTS_CSV.toFile().isFile();
    }
}
