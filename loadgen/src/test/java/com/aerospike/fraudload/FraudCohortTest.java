package com.aerospike.fraudload;

import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

class FraudCohortTest {
    @Test
    void selectsRandomDistinctAccountsFromPool() {
        AccountPool pool = AccountPool.deterministic(10_000, "Account");
        FraudCohort a = FraudCohort.select(pool, 5, 5, 42L);
        FraudCohort b = FraudCohort.select(pool, 5, 5, 42L);

        assertEquals(5, a.muleCount());
        assertEquals(5, a.fraudsterCount());
        assertArrayEquals(a.mules(), b.mules());
        assertArrayEquals(a.fraudsters(), b.fraudsters());

        Set<String> all = new HashSet<>();
        for (String id : a.mules()) all.add(id);
        for (String id : a.fraudsters()) all.add(id);
        assertEquals(10, all.size());

        // Not the first 10 accounts in the pool
        assertFalse(all.contains("Account1"));
        assertFalse(all.contains("Account2"));
    }

    @Test
    void differentSeedsProduceDifferentCohorts() {
        AccountPool pool = AccountPool.deterministic(1000, "Account");
        FraudCohort a = FraudCohort.select(pool, 3, 3, 1L);
        FraudCohort b = FraudCohort.select(pool, 3, 3, 2L);
        assertFalse(Set.of(a.mules()[0]).equals(Set.of(b.mules()[0])));
    }

    @Test
    void randomDistinctIndicesAreUniqueAndInRange() {
        int[] idx = FraudCohort.randomDistinctIndices(1_000_000, 400, new java.util.Random(99L));
        assertEquals(400, idx.length);
        Set<Integer> seen = new HashSet<>();
        for (int i : idx) {
            assertTrue(i >= 0 && i < 1_000_000);
            assertTrue(seen.add(i));
        }
    }
}
