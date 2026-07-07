package com.aerospike.fraudload;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

/** Runs KV and graph writes in parallel; both must succeed. */
public final class PairedWriter {
    private final KvWriter kvWriter;
    private final GraphWriter graphWriter;

    public PairedWriter(KvWriter kvWriter, GraphWriter graphWriter) {
        this.kvWriter = kvWriter;
        this.graphWriter = graphWriter;
    }

    /**
     * Uses the caller's pair executor (typically 2 threads per worker) so KV and graph
     * round-trips overlap without spawning threads per transaction.
     */
    public void writeTransaction(Transaction t, java.util.concurrent.ExecutorService pairExecutor,
                               int workerIndex) throws Exception {
        AtomicReference<Exception> failure = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(2);

        pairExecutor.execute(() -> {
            try {
                kvWriter.writeTransaction(t);
            } catch (Exception e) {
                failure.compareAndSet(null, e);
            } finally {
                done.countDown();
            }
        });
        pairExecutor.execute(() -> {
            try {
                graphWriter.writeEdge(t, workerIndex);
            } catch (Exception e) {
                failure.compareAndSet(null, e);
            } finally {
                done.countDown();
            }
        });

        done.await();
        Exception err = failure.get();
        if (err != null) {
            throw err;
        }
    }
}
