package com.aerospike.fraudload;

/** Immutable run configuration. targetRatePerSec <= 0 means unbounded (run flat-out). */
public record Config(
        String host,
        int port,
        String namespace,
        AccountPool accountPool,
        int workers,
        long targetRatePerSec,
        int durationSeconds,
        boolean updateBalances,
        WriteMode writeMode,
        String graphHost,
        int graphPort,
        double fraudRatio,
        String accountPrefix,
        String userPrefix) {
}
