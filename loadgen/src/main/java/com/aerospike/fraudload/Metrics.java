package com.aerospike.fraudload;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.util.concurrent.atomic.LongAdder;

/** Lightweight counters only — never stores per-op records. */
public final class Metrics {
    private static final Logger log = LoggerFactory.getLogger(Metrics.class);
    private final LongAdder txns = new LongAdder();
    private final LongAdder errors = new LongAdder();

    public void recordTxn() { txns.increment(); }
    public void recordError() { errors.increment(); }
    public long txns() { return txns.sum(); }
    public long errors() { return errors.sum(); }

    public Thread startReporter() {
        Thread t = new Thread(() -> {
            long last = 0;
            try {
                while (!Thread.currentThread().isInterrupted()) {
                    Thread.sleep(1000);
                    long now = txns.sum();
                    log.info("throughput={} txn/s total={} errors={}", now - last, now, errors.sum());
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
