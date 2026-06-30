package com.aerospike.fraudload;

import com.aerospike.client.AerospikeClient;
import com.aerospike.client.policy.ClientPolicy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Random;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Runs workers threads writing transactions until duration elapses. */
public final class LoadDriver {
    private static final Logger log = LoggerFactory.getLogger(LoadDriver.class);

    private final Config cfg;
    private final Metrics metrics = new Metrics();

    public LoadDriver(Config cfg) { this.cfg = cfg; }

    public Metrics metrics() { return metrics; }

    public void run() throws InterruptedException {
        ClientPolicy cp = new ClientPolicy();
        cp.maxConnsPerNode = Math.max(300, cfg.workers() * 8);
        try (AerospikeClient client = new AerospikeClient(cp, cfg.host(), cfg.port())) {
            KvWriter writer = new KvWriter(client, cfg.namespace(), cfg.updateBalances());
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(cfg.durationSeconds());
            long perWorkerNanosPerOp = cfg.targetRatePerSec() <= 0 ? 0
                    : TimeUnit.SECONDS.toNanos(1) * cfg.workers() / cfg.targetRatePerSec();

            CountDownLatch done = new CountDownLatch(cfg.workers());
            Thread reporter = metrics.startReporter();
            for (int w = 0; w < cfg.workers(); w++) {
                final int workerIndex = w;
                Thread thread = new Thread(() -> {
                    Random rnd = new Random(0x5DEECE66DL ^ workerIndex);
                    TransactionGenerator gen = new TransactionGenerator(cfg.accountCount(), rnd);
                    long next = System.nanoTime();
                    try {
                        while (System.nanoTime() < deadline) {
                            if (perWorkerNanosPerOp > 0) {
                                long wait = next - System.nanoTime();
                                if (wait > 0) parkNanos(wait);
                                next += perWorkerNanosPerOp;
                            }
                            try {
                                writer.writeTransaction(gen.next());
                                metrics.recordTxn();
                            } catch (Exception e) {
                                metrics.recordError();
                            }
                        }
                    } finally {
                        done.countDown();
                    }
                }, "loadgen-worker-" + w);
                thread.start();
            }
            done.await();
            reporter.interrupt();
            log.info("run complete: total={} errors={}", metrics.txns(), metrics.errors());
        }
    }

    private static void parkNanos(long nanos) {
        java.util.concurrent.locks.LockSupport.parkNanos(nanos);
    }
}
