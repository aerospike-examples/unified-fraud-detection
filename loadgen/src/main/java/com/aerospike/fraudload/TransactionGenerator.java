package com.aerospike.fraudload;

import java.time.Instant;
import java.util.Random;
import java.util.UUID;

/** Generates uniform-random synthetic transactions over [0, accountCount). */
public final class TransactionGenerator {
    private static final String[] TYPES = {"transfer", "payment", "purchase", "withdrawal", "deposit"};
    private static final String[] LOCATIONS = {"New York, NY", "Chicago, IL", "Austin, TX", "Seattle, WA"};

    private final int accountCount;
    private final Random random;

    public TransactionGenerator(int accountCount, Random random) {
        if (accountCount < 2) throw new IllegalArgumentException("need >= 2 accounts");
        this.accountCount = accountCount;
        this.random = random;
    }

    public Transaction next() {
        int s = random.nextInt(accountCount);
        int r = random.nextInt(accountCount - 1);
        if (r >= s) r++;
        long amountCents = 5_000L + (long) (random.nextDouble() * 1_495_000L);
        return new Transaction(
                UUID.randomUUID().toString(),
                "acct-" + s,
                "acct-" + r,
                amountCents,
                TYPES[random.nextInt(TYPES.length)],
                LOCATIONS[random.nextInt(LOCATIONS.length)],
                Instant.now());
    }
}
