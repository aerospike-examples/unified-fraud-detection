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

    public AccountPool(String[] ids) {
        if (ids.length < 2) {
            throw new IllegalArgumentException("need >= 2 account ids");
        }
        this.ids = ids.clone();
    }

    public int size() {
        return ids.length;
    }

    public String idAt(int index) {
        return ids[index];
    }

    /** Synthetic IDs for KV-only runs without a seeded graph. */
    public static AccountPool synthetic(int count) {
        if (count < 2) throw new IllegalArgumentException("need >= 2 accounts");
        String[] ids = new String[count];
        for (int i = 0; i < count; i++) {
            ids[i] = "acct-" + i;
        }
        return new AccountPool(ids);
    }

    /**
     * Reads the graph bulk-load accounts CSV (~id in the first column).
     * {@code maxAccounts} caps how many rows are loaded (0 = all).
     */
    public static AccountPool fromCsv(Path path, int maxAccounts) throws IOException {
        List<String> ids = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(path)) {
            String line = reader.readLine();
            if (line == null) {
                throw new IllegalArgumentException("empty accounts file: " + path);
            }
            while ((line = reader.readLine()) != null) {
                if (maxAccounts > 0 && ids.size() >= maxAccounts) break;
                int comma = line.indexOf(',');
                String id = (comma < 0 ? line : line.substring(0, comma)).trim();
                if (!id.isEmpty()) {
                    ids.add(id);
                }
            }
        }
        if (ids.size() < 2) {
            throw new IllegalArgumentException("accounts file has < 2 ids: " + path);
        }
        return new AccountPool(ids.toArray(String[]::new));
    }
}
