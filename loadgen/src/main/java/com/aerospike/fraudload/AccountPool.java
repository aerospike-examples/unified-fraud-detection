package com.aerospike.fraudload;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Account IDs used as transaction endpoints (must exist in graph for edge writes). */
public final class AccountPool {
    private final String[] ids;
    private final String prefix;
    private final int count;

    private AccountPool(String[] ids, String prefix, int count) {
        if (count < 2) {
            throw new IllegalArgumentException("need >= 2 accounts");
        }
        this.ids = ids;
        this.prefix = prefix;
        this.count = count;
    }

    public int size() {
        return count;
    }

    public String idAt(int index) {
        if (index < 0 || index >= count) {
            throw new IndexOutOfBoundsException(index);
        }
        if (ids != null) {
            return ids[index];
        }
        return prefix + (index + 1);
    }

    /**
     * Deterministic IDs matching {@code scripts/generate-ags-csv.py}: Account1, Account2, …
     * (1-based). Generated on demand — safe for billion-scale pools.
     */
    public static AccountPool deterministic(int count, String prefix) {
        if (prefix == null || prefix.isBlank()) {
            throw new IllegalArgumentException("account prefix required");
        }
        return new AccountPool(null, prefix, count);
    }

    /** Legacy 0-based acct-N ids for KV-only tests without a seeded graph. */
    public static AccountPool synthetic(int count) {
        String[] ids = new String[count];
        for (int i = 0; i < count; i++) {
            ids[i] = "acct-" + i;
        }
        return new AccountPool(ids, null, count);
    }

    /**
     * Reads the graph bulk-load accounts CSV (~id in the first column).
     * {@code maxAccounts} caps how many rows are loaded (0 = all).
     */
    public static AccountPool fromCsv(Path path, int maxAccounts) throws IOException {
        List<String> loaded = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(path)) {
            String line = reader.readLine();
            if (line == null) {
                throw new IllegalArgumentException("empty accounts file: " + path);
            }
            while ((line = reader.readLine()) != null) {
                if (maxAccounts > 0 && loaded.size() >= maxAccounts) break;
                int comma = line.indexOf(',');
                String id = (comma < 0 ? line : line.substring(0, comma)).trim();
                if (!id.isEmpty()) {
                    loaded.add(id);
                }
            }
        }
        if (loaded.size() < 2) {
            throw new IllegalArgumentException("accounts file has < 2 ids: " + path);
        }
        return new AccountPool(loaded.toArray(String[]::new), null, loaded.size());
    }
}
