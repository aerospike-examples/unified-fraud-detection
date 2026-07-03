package com.aerospike.fraudload;

import java.util.Random;

/**
 * A fixed, globally-shared set of accounts that participate in fraud:
 *   - mules:      receive concentrated fan-in (money-mule pattern)
 *   - fraudsters: originate rapid fan-out / bursts of high-value transfers
 *
 * Members are chosen deterministically from the account pool so every worker
 * agrees on the cohort, and so the exact set is known up front — that is what
 * lets us populate the review queue without ever scanning the full dataset.
 */
public final class FraudCohort {
    private final String[] mules;
    private final String[] fraudsters;

    private FraudCohort(String[] mules, String[] fraudsters) {
        this.mules = mules;
        this.fraudsters = fraudsters;
    }

    /** Selects the first {@code mules}/{@code fraudsters} accounts from the pool (disjoint). */
    public static FraudCohort select(AccountPool pool, int mules, int fraudsters) {
        int total = mules + fraudsters;
        if (total <= 0) {
            return new FraudCohort(new String[0], new String[0]);
        }
        if (total > pool.size()) {
            throw new IllegalArgumentException(
                    "cohort (" + total + ") exceeds account pool (" + pool.size() + ")");
        }
        String[] m = new String[mules];
        for (int i = 0; i < mules; i++) {
            m[i] = pool.idAt(i);
        }
        String[] f = new String[fraudsters];
        for (int i = 0; i < fraudsters; i++) {
            f[i] = pool.idAt(mules + i);
        }
        return new FraudCohort(m, f);
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
