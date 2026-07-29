package com.aerospike.fraudload;

import com.aerospike.client.AerospikeClient;
import com.aerospike.client.Bin;
import com.aerospike.client.Key;
import com.aerospike.client.policy.ClientPolicy;
import com.aerospike.client.policy.WritePolicy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Instant;
import java.util.Random;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** Runs worker threads writing transactions until duration elapses. */
public final class LoadDriver {
    private static final Logger log = LoggerFactory.getLogger(LoadDriver.class);

    private final Config cfg;
    private final Metrics metrics = new Metrics();
    private long baselineTxns = 0L;

    public LoadDriver(Config cfg) { this.cfg = cfg; }

    public Metrics metrics() { return metrics; }

    public void run() throws InterruptedException {
        ClientPolicy cp = new ClientPolicy();
        cp.maxConnsPerNode = Math.max(300, cfg.workers() * 8);
        // Same fix as GremlinPool: the Aerospike client otherwise opens KV
        // connections lazily on demand, so a burst of concurrent workers at run
        // start pays one-time connect latency inline on the first requests
        // instead of it being paid up front. minConnsPerNode preallocates the
        // full pool at client construction.
        cp.minConnsPerNode = cp.maxConnsPerNode;

        GraphWriter graphWriter = null;
        GremlinPool gremlinPool = null;
        if (cfg.writeMode().writesGraph()) {
            gremlinPool = new GremlinPool(cfg.graphHost(), cfg.graphPort(), cfg.workers());
            graphWriter = new GraphWriter(gremlinPool);
            try {
                baselineTxns = GraphSummary.fetch(cfg.graphHost(), cfg.graphPort()).transacts();
                log.info("baseline TRANSACTS from graph summary: {}", baselineTxns);
            } catch (Exception e) {
                log.warn("could not read graph summary for txn baseline: {}", e.toString());
            }
        }

        try (AerospikeClient client = cfg.writeMode().writesKv()
                ? new AerospikeClient(cp, cfg.host(), cfg.port()) : null;
             GremlinPool gremlin = gremlinPool) {

            final GraphWriter graph = graphWriter;
            KvWriter kvWriter = client != null
                    ? new KvWriter(client, cfg.namespace(), cfg.updateBalances()) : null;
            PairedWriter pairedWriter = (kvWriter != null && graph != null)
                    ? new PairedWriter(kvWriter, graph) : null;

            final FraudInjector fraudInjector;
            if (client != null && cfg.fraudRatio() > 0.0) {
                fraudInjector = new FraudInjector(client, cfg.namespace(), graph,
                        cfg.accountPrefix(), cfg.userPrefix());
                fraudInjector.beginRun();
            } else {
                fraudInjector = null;
                if (cfg.fraudRatio() > 0.0 && client == null) {
                    log.warn("fraud-ratio set but write mode has no KV client; "
                            + "live flags require --mode kv or paired");
                }
            }

            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(cfg.durationSeconds());
            long perWorkerNanosPerOp = cfg.targetRatePerSec() <= 0 ? 0
                    : TimeUnit.SECONDS.toNanos(1) * cfg.workers() / cfg.targetRatePerSec();

            CountDownLatch done = new CountDownLatch(cfg.workers());
            Thread reporter = metrics.startReporter(cfg.writeMode());
            // Live dashboard aggregate: refresh amount + fraud_rate every 5s so the
            // remote-mode dashboard shows real, moving numbers during the demo.
            Thread aggregateWriter = client != null ? startAggregateWriter(client, cfg.namespace()) : null;
            ExecutorService[] pairExecutors = new ExecutorService[cfg.workers()];
            for (int w = 0; w < cfg.workers(); w++) {
                if (cfg.writeMode() == WriteMode.paired) {
                    final int pairWorker = w;
                    pairExecutors[w] = Executors.newFixedThreadPool(2, r -> {
                        Thread t = new Thread(r, "pair-" + pairWorker);
                        t.setDaemon(true);
                        return t;
                    });
                }
            }

            for (int w = 0; w < cfg.workers(); w++) {
                final int workerIndex = w;
                final ExecutorService pairExecutor = pairExecutors[w];
                Thread thread = new Thread(() -> {
                    Random rnd = new Random(0x5DEECE66DL ^ workerIndex);
                    KeyShard shard = new KeyShard(workerIndex, cfg.workers(), cfg.accountPool().size());
                    TransactionGenerator gen = new TransactionGenerator(cfg.accountPool(), shard, rnd,
                            cfg.ringPoolSize(), cfg.ringRatio());
                    boolean fraudEnabled = cfg.fraudRatio() > 0.0;
                    long next = System.nanoTime();
                    try {
                        while (System.nanoTime() < deadline) {
                            if (perWorkerNanosPerOp > 0) {
                                long wait = next - System.nanoTime();
                                if (wait > 0) parkNanos(wait);
                                next += perWorkerNanosPerOp;
                            }
                            try {
                                Transaction t = (fraudEnabled && rnd.nextDouble() < cfg.fraudRatio())
                                        ? gen.fraudTransaction()
                                        : gen.next();
                                switch (cfg.writeMode()) {
                                    case kv -> kvWriter.writeTransaction(t);
                                    case graph -> graph.writeEdge(t, workerIndex);
                                    case paired -> pairedWriter.writeTransaction(t, pairExecutor, workerIndex);
                                }
                                if (t.fraud() && fraudInjector != null) {
                                    fraudInjector.flagOnDetection(t);
                                }
                                metrics.recordTxn();
                                metrics.recordAmount(t.amountCents());
                                if (t.fraud()) metrics.recordFraud();
                                metrics.recordDisposition(t.fraud(), t.fraudScore());
                            } catch (Exception e) {
                                metrics.recordError();
                                if (metrics.errors() == 1) {
                                    log.warn("first write error (subsequent suppressed): {}", e.toString());
                                }
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
            if (aggregateWriter != null) {
                aggregateWriter.interrupt();
                writeAggregate(client, cfg.namespace()); // final snapshot
            }
            if (fraudInjector != null) {
                fraudInjector.close(); // stops feed flusher + final fraud_feed snapshot
            }
            for (ExecutorService ex : pairExecutors) {
                if (ex != null) {
                    ex.shutdown();
                    ex.awaitTermination(5, TimeUnit.SECONDS);
                }
            }

            log.info("run complete: mode={} total={} errors={}",
                    cfg.writeMode(), metrics.txns(), metrics.errors());
        }
    }

    private Thread startAggregateWriter(AerospikeClient client, String namespace) {
        Thread t = new Thread(() -> {
            try {
                while (!Thread.currentThread().isInterrupted()) {
                    Thread.sleep(5000);
                    writeAggregate(client, namespace);
                }
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }, "aggregate-writer");
        t.setDaemon(true);
        t.start();
        return t;
    }

    /**
     * Writes the optional `config:aggregate_stats` record the backend reads in
     * remote mode. Includes live txn count (baseline graph summary + this run) so
     * the dashboard updates during the demo without waiting for AGS metadata lag.
     *
     * blocked/review/clean aren't tracked historically (billions of past edges
     * were never bucketed), so we extrapolate: this run's own blocked:review:clean
     * ratio is applied to the full (baseline + run) txn count. That keeps the
     * three figures always summing to `txns` instead of showing a tiny live-run
     * count next to a billion-scale total.
     */
    private void writeAggregate(AerospikeClient client, String namespace) {
        try {
            long totalTxns = baselineTxns + metrics.txns();
            long runTxns = metrics.txns();
            long blocked = 0;
            long review = 0;
            if (runTxns > 0) {
                blocked = Math.round(totalTxns * (metrics.blockedTxns() / (double) runTxns));
                review = Math.round(totalTxns * (metrics.reviewTxns() / (double) runTxns));
            }
            long clean = Math.max(0, totalTxns - blocked - review);

            WritePolicy wp = new WritePolicy(client.writePolicyDefault);
            Key key = new Key(namespace, "config", "aggregate_stats");
            client.put(wp, key,
                    new Bin("total_amount", Math.round(metrics.totalAmount() * 100.0) / 100.0),
                    new Bin("fraud_rate", Math.round(metrics.fraudRatePct() * 100.0) / 100.0),
                    new Bin("txns", totalTxns),
                    new Bin("blocked", blocked),
                    new Bin("review", review),
                    new Bin("clean", clean),
                    new Bin("last_updated", Instant.now().toString()));
        } catch (Exception e) {
            log.debug("aggregate write failed: {}", e.toString());
        }
    }

    private static void parkNanos(long nanos) {
        java.util.concurrent.locks.LockSupport.parkNanos(nanos);
    }
}
