package com.aerospike.fraudload;

/** Immutable run configuration. targetRatePerSec <= 0 means unbounded (run flat-out). */
public record Config(
        String host,
        int port,
        String namespace,
        int accountCount,
        int workers,
        long targetRatePerSec,
        int durationSeconds,
        boolean updateBalances) {
}
