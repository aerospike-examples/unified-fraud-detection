package com.aerospike.fraudload;

import java.util.Random;

/**
 * Fixed-size ring of primary keys this worker has already written, so a mixed
 * workload reads records that actually exist.
 *
 * <p>Reading random keys out of a billion-account space would almost always
 * miss, and a miss is far cheaper server-side than a real read — it never
 * touches the device — so a miss-heavy run would report throughput Aerospike
 * isn't really doing. Not synchronized: each worker owns its own instance.
 */
final class RecentKeys {
    private final String[] buf;
    private long written;

    RecentKeys(int capacity) {
        this.buf = new String[Math.max(1, capacity)];
    }

    void add(String key) {
        buf[(int) Math.floorMod(written++, buf.length)] = key;
    }

    /** A previously written key, or null until at least one write has landed. */
    String pick(Random rnd) {
        int size = (int) Math.min(written, buf.length);
        return size == 0 ? null : buf[rnd.nextInt(size)];
    }
}
