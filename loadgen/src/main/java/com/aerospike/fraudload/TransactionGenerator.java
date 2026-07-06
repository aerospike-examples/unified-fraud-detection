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

    public TransactionGenerator(AccountPool pool, Random random) {
        this(pool, 0, pool.size(), random);
    }

    /** Restricts sender/receiver picks to [firstIndex, endIndex) within the pool. */
    public TransactionGenerator(AccountPool pool, int firstIndex, int endIndex, Random random) {
        if (endIndex - firstIndex < 2) {
            throw new IllegalArgumentException("shard needs >= 2 accounts");
        }
        this.pool = pool;
        this.firstIndex = firstIndex;
        this.endIndex = endIndex;
        this.random = random;
    }

    public TransactionGenerator(AccountPool pool, KeyShard shard, Random random) {
        this(pool, shard.firstIndex(), shard.endIndex(), random);
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

    /**
     * Fraudulent transaction between random accounts in this shard. Alternates fan-out
     * (flag sender) and fan-in (flag receiver) patterns. No fixed cohort — mimics live
     * detection on suspicious activity anywhere in the graph.
     */
    public Transaction fraudTransaction() {
        boolean fanOut = random.nextBoolean();
        String sender;
        String receiver;
        String alertAccount;
        String typology;
        if (fanOut) {
            sender = randomShardAccount();
            receiver = randomShardAccountOtherThan(sender);
            alertAccount = sender;
            typology = "fraudster";
        } else {
            receiver = randomShardAccount();
            sender = randomShardAccountOtherThan(receiver);
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
