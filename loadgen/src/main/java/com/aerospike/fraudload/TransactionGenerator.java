package com.aerospike.fraudload;

import java.time.Instant;
import java.util.Random;
import java.util.UUID;

/** Generates uniform-random synthetic transactions over an account pool (optionally sharded). */
public final class TransactionGenerator {
    private static final String[] TYPES = {"transfer", "payment", "purchase", "withdrawal", "deposit"};
    private static final String[] LOCATIONS = {"New York, NY", "Chicago, IL", "Austin, TX", "Seattle, WA"};

    private final AccountPool pool;
    private final int firstIndex;
    private final int endIndex;
    private final Random random;
    private final int ringPoolSize;
    private final double ringRatio;

    // Small rotating cohort of account indices (within this shard) that ring-mode
    // fraud transactions are biased towards, so repeated fraud txns build up the
    // dense, reciprocal, triangle-rich structure detect_fraud_ring looks for —
    // see fraudTransaction() below. Rotated periodically (rather than fixed for
    // the whole run) so a long-running loadgen spreads ring structure across many
    // different accounts over time instead of only ever the first 12 per worker.
    private int[] ringPool;
    private int ringTxnsSinceRotate;

    public TransactionGenerator(AccountPool pool, Random random) {
        this(pool, 0, pool.size(), random, 0, 0.0);
    }

    /** Restricts sender/receiver picks to [firstIndex, endIndex) within the pool. */
    public TransactionGenerator(AccountPool pool, int firstIndex, int endIndex, Random random) {
        this(pool, firstIndex, endIndex, random, 0, 0.0);
    }

    public TransactionGenerator(AccountPool pool, int firstIndex, int endIndex, Random random,
                                 int ringPoolSize, double ringRatio) {
        if (endIndex - firstIndex < 2) {
            throw new IllegalArgumentException("shard needs >= 2 accounts");
        }
        this.pool = pool;
        this.firstIndex = firstIndex;
        this.endIndex = endIndex;
        this.random = random;
        this.ringPoolSize = Math.max(0, Math.min(ringPoolSize, endIndex - firstIndex));
        this.ringRatio = ringRatio;
    }

    public TransactionGenerator(AccountPool pool, KeyShard shard, Random random) {
        this(pool, shard.firstIndex(), shard.endIndex(), random, 0, 0.0);
    }

    public TransactionGenerator(AccountPool pool, KeyShard shard, Random random,
                                 int ringPoolSize, double ringRatio) {
        this(pool, shard.firstIndex(), shard.endIndex(), random, ringPoolSize, ringRatio);
    }

    public Transaction next() {
        int span = endIndex - firstIndex;
        int s = firstIndex + random.nextInt(span);
        int r = firstIndex + random.nextInt(span - 1);
        if (r >= s) r++;
        long amountCents = 5_000L + (long) (random.nextDouble() * 1_495_000L);
        return new Transaction(
                UUID.randomUUID().toString(),
                pool.idAt(s),
                pool.idAt(r),
                amountCents,
                TYPES[random.nextInt(TYPES.length)],
                LOCATIONS[random.nextInt(LOCATIONS.length)],
                Instant.now());
    }

    private String randomShardAccount() {
        int span = endIndex - firstIndex;
        return pool.idAt(firstIndex + random.nextInt(span));
    }

    private String randomShardAccountOtherThan(String excluded) {
        int span = endIndex - firstIndex;
        if (span <= 1) {
            return excluded;
        }
        String id = randomShardAccount();
        int guard = 0;
        while (id.equals(excluded) && guard++ < 8) {
            id = randomShardAccount();
        }
        return id;
    }

    // Rotate the ring pool after this many ring-mode fraud txns have landed on it —
    // enough repeated random pairs within a pool of ringPoolSize to statistically
    // build up several reciprocal partners and triangles (detect_fraud_ring's two
    // strongest structural signals) before moving on to a fresh set of accounts.
    private static final int ROTATE_EVERY_MULTIPLIER = 4;

    private void ensureRingPool() {
        int span = endIndex - firstIndex;
        if (ringPool == null || ringTxnsSinceRotate >= ringPoolSize * ROTATE_EVERY_MULTIPLIER) {
            int size = Math.min(ringPoolSize, span);
            ringPool = new int[size];
            // Sample without replacement within the shard so the pool has `size`
            // distinct accounts (Fisher-Yates over a lazily-built index window).
            java.util.HashSet<Integer> picked = new java.util.HashSet<>();
            for (int i = 0; i < size; i++) {
                int idx;
                do {
                    idx = firstIndex + random.nextInt(span);
                } while (!picked.add(idx));
                ringPool[i] = idx;
            }
            ringTxnsSinceRotate = 0;
        }
    }

    private String ringPoolAccount() {
        return pool.idAt(ringPool[random.nextInt(ringPool.length)]);
    }

    private String ringPoolAccountOtherThan(String excluded) {
        if (ringPool.length <= 1) {
            return excluded;
        }
        String id = ringPoolAccount();
        int guard = 0;
        while (id.equals(excluded) && guard++ < 8) {
            id = ringPoolAccount();
        }
        return id;
    }

    /**
     * Fraudulent transaction. With probability `ringRatio`, both sender and receiver
     * are drawn from a small rotating per-worker cohort (see ensureRingPool()) instead
     * of anywhere in the shard — repeated fraud txns landing on the same ~12 accounts
     * is what builds the dense, reciprocal, triangle-rich structure detect_fraud_ring
     * looks for. The rest of the time, falls back to the original fully-random
     * fan-out/fan-in pattern (isolated one-off fraud, no ring), so not every flagged
     * account ends up looking like organized ring fraud.
     */
    public Transaction fraudTransaction() {
        boolean useRing = ringPoolSize >= 2 && ringRatio > 0.0 && random.nextDouble() < ringRatio;
        if (useRing) {
            ensureRingPool();
            ringTxnsSinceRotate++;
        }

        boolean fanOut = random.nextBoolean();
        String sender;
        String receiver;
        String alertAccount;
        String typology;
        if (fanOut) {
            sender = useRing ? ringPoolAccount() : randomShardAccount();
            receiver = useRing ? ringPoolAccountOtherThan(sender) : randomShardAccountOtherThan(sender);
            alertAccount = sender;
            typology = "fraudster";
        } else {
            receiver = useRing ? ringPoolAccount() : randomShardAccount();
            sender = useRing ? ringPoolAccountOtherThan(receiver) : randomShardAccountOtherThan(receiver);
            alertAccount = receiver;
            typology = "money_mule";
        }

        long amountCents = 300_000L + (long) (random.nextDouble() * 4_700_000L); // $3k-$50k
        double score = Math.round((70.0 + random.nextDouble() * 30.0) * 100.0) / 100.0;
        return new Transaction(
                UUID.randomUUID().toString(),
                sender,
                receiver,
                amountCents,
                TYPES[random.nextInt(TYPES.length)],
                LOCATIONS[random.nextInt(LOCATIONS.length)],
                Instant.now(),
                true,
                score,
                alertAccount,
                typology);
    }
}
