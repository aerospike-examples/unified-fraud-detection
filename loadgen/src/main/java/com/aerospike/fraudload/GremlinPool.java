package com.aerospike.fraudload;

import org.apache.tinkerpop.gremlin.driver.Client;
import org.apache.tinkerpop.gremlin.driver.Cluster;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Long-lived Gremlin cluster with one {@link Client} per loadgen worker.
 *
 * A single shared Client under high concurrency (64–128 workers each doing
 * blocking submit().get()) causes the TinkerPop pool to constantly mark
 * connections dead and replace them ("Replace Connection" spam in logs).
 * Pinning one aliased client per worker keeps in-flight requests stable and
 * sizes the websocket pool to match actual parallelism.
 */
public final class GremlinPool implements AutoCloseable {
    private static final Logger log = LoggerFactory.getLogger(GremlinPool.class);

    private final Cluster cluster;
    private final Client[] workerClients;

    public GremlinPool(String host, int port, int workers) {
        int w = Math.max(1, workers);
        int maxPool = Math.max(16, w * 2);
        int minPool = Math.max(8, w / 2);
        cluster = Cluster.build()
                .addContactPoint(host)
                .port(port)
                .minConnectionPoolSize(minPool)
                .maxConnectionPoolSize(maxPool)
                // Multiplex several in-flight requests per websocket so we need
                // fewer physical connections under 50k+ txn/s.
                .maxInProcessPerConnection(32)
                .maxWaitForConnection(30_000)
                .create();
        workerClients = new Client[w];
        for (int i = 0; i < w; i++) {
            // Do NOT use .alias() — AGS only exposes the global "g" traversal source;
            // aliasing to loadgen-worker-N causes ResponseException on every submit.
            workerClients[i] = cluster.connect();
        }
        log.info("Gremlin pool ready at {}:{} (workers={}, minPool={}, maxPool={}, maxInProcess=32)",
                host, port, w, minPool, maxPool);
    }

    public Client clientForWorker(int workerIndex) {
        return workerClients[Math.floorMod(workerIndex, workerClients.length)];
    }

    /** Shared client for rare admin/read calls (resolveOwner, etc.). */
    public Client sharedClient() {
        return workerClients[0];
    }

    @Override
    public void close() {
        for (Client c : workerClients) {
            if (c != null) {
                c.close();
            }
        }
        if (cluster != null) {
            cluster.close();
        }
    }
}
