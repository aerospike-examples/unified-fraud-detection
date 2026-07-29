package com.aerospike.fraudload;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.util.concurrent.atomic.LongAdder;

/** Lightweight counters only — never stores per-op records. */
public final class Metrics {
    private static final Logger log = LoggerFactory.getLogger(Metrics.class);
    private final LongAdder txns = new LongAdder();
    private final LongAdder reads = new LongAdder();
    private final LongAdder errors = new LongAdder();
    private final LongAdder fraudTxns = new LongAdder();
    private final LongAdder amountCents = new LongAdder();
    // Disposition breakdown for THIS run's transactions only, mirroring the
    // backend's local-mode bucketing convention (fraud_score >= 90 -> blocked,
    // else review). Used to extrapolate a blocked/review/clean split over the
    // full (baseline + run) transaction count — see LoadDriver.writeAggregate.
    private static final double BLOCKED_SCORE_THRESHOLD = 90.0;
    private final LongAdder blockedTxns = new LongAdder();
    private final LongAdder reviewTxns = new LongAdder();

    public void recordTxn() { txns.increment(); }
    public void recordRead() { reads.increment(); }
    public void recordError() { errors.increment(); }
    public void recordAmount(long cents) { amountCents.add(cents); }
    public void recordFraud() { fraudTxns.increment(); }
    public long txns() { return txns.sum(); }
    public long reads() { return reads.sum(); }

    /**
     * Total Aerospike operations. Reported throughput counts reads too, while
     * {@link #txns()} stays write-only so the dashboard's transaction totals
     * aren't inflated by a read-heavy run.
     */
    public long ops() { return txns.sum() + reads.sum(); }

    public long errors() { return errors.sum(); }
    public long fraudTxns() { return fraudTxns.sum(); }
    public double totalAmount() { return amountCents.sum() / 100.0; }

    /** Record a transaction's fraud disposition (no-op for clean transactions). */
    public void recordDisposition(boolean fraud, double fraudScore) {
        if (!fraud) return;
        if (fraudScore >= BLOCKED_SCORE_THRESHOLD) {
            blockedTxns.increment();
        } else {
            reviewTxns.increment();
        }
    }

    public long blockedTxns() { return blockedTxns.sum(); }
    public long reviewTxns() { return reviewTxns.sum(); }

    /** Fraud rate as a percentage of processed transactions (0 when none yet). */
    public double fraudRatePct() {
        long t = txns.sum();
        return t == 0 ? 0.0 : (fraudTxns.sum() * 100.0) / t;
    }

    public Thread startReporter(WriteMode mode) {
        Thread t = new Thread(() -> {
            long last = 0;
            try {
                while (!Thread.currentThread().isInterrupted()) {
                    Thread.sleep(1000);
                    long now = ops();
                    log.info("mode={} throughput={} op/s total={} writes={} reads={} errors={}",
                            mode, now - last, now, txns.sum(), reads.sum(), errors.sum());
                    last = now;
                }
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }, "metrics-reporter");
        t.setDaemon(true);
        t.start();
        return t;
    }
}
