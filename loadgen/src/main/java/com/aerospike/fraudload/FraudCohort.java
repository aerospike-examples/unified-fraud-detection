package com.aerospike.fraudload;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Random;
import java.util.Set;

/**
 * A fixed, globally-shared set of accounts that participate in fraud:
 *   - mules:      receive concentrated fan-in (money-mule pattern)
 *   - fraudsters: originate rapid fan-out / bursts of high-value transfers
 *
 * Members are chosen at random (distinct indices into the account pool) with a
 * shared seed so every worker agrees on the cohort, and so the exact set is
 * known up front for populating the review queue without scanning the graph.
 */
public final class FraudCohort {
    private final String[] mules;
    private final String[] fraudsters;

    private FraudCohort(String[] mules, String[] fraudsters) {
        this.mules = mules;
        this.fraudsters = fraudsters;
    }

    /** Random distinct pool indices; uses {@code seed} so all workers share one cohort. */
    public static FraudCohort select(AccountPool pool, int mules, int fraudsters, long seed) {
        int total = mules + fraudsters;
        if (total <= 0) {
            return new FraudCohort(new String[0], new String[0]);
        }
        if (total > pool.size()) {
            throw new IllegalArgumentException(
                    "cohort (" + total + ") exceeds account pool (" + pool.size() + ")");
        }
        int[] indices = randomDistinctIndices(pool.size(), total, new Random(seed));
        String[] m = new String[mules];
        for (int i = 0; i < mules; i++) {
            m[i] = pool.idAt(indices[i]);
        }
        String[] f = new String[fraudsters];
        for (int i = 0; i < fraudsters; i++) {
            f[i] = pool.idAt(indices[mules + i]);
        }
        return new FraudCohort(m, f);
    }

    /** Picks {@code count} distinct indices uniformly from [0, poolSize). */
    static int[] randomDistinctIndices(int poolSize, int count, Random rnd) {
        if (count > poolSize) {
            throw new IllegalArgumentException("count " + count + " exceeds pool " + poolSize);
        }
        Set<Integer> picked = new HashSet<>(count * 2);
        while (picked.size() < count) {
            picked.add(rnd.nextInt(poolSize));
        }
        List<Integer> list = new ArrayList<>(picked);
        return list.stream().mapToInt(Integer::intValue).toArray();
    }

    public boolean isEmpty() {
        return mules.length == 0 && fraudsters.length == 0;
    }

    public int muleCount() { return mules.length; }
    public int fraudsterCount() { return fraudsters.length; }
    public String[] mules() { return mules.clone(); }
    public String[] fraudsters() { return fraudsters.clone(); }

    /** Thread-safe: caller supplies its own Random. */
    public String randomMule(Random rnd) {
        return mules.length == 0 ? null : mules[rnd.nextInt(mules.length)];
    }

    public String randomFraudster(Random rnd) {
        return fraudsters.length == 0 ? null : fraudsters[rnd.nextInt(fraudsters.length)];
    }
}
