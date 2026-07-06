package com.aerospike.fraudload;

import org.apache.tinkerpop.gremlin.driver.Client;
import org.apache.tinkerpop.gremlin.driver.Cluster;
import org.apache.tinkerpop.gremlin.driver.Result;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/** Reads AGS metadata summary (cheap at billion scale — no full-graph scan). */
public final class GraphSummary {
    private static final Logger log = LoggerFactory.getLogger(GraphSummary.class);

    // Do not append .next() — on AGS that yields the first map entry, not the full summary.
    private static final String SUMMARY_GREMLIN =
            "g.call('aerospike.graph.admin.metadata.summary')";

    private GraphSummary() {}

    public record Counts(long users, long accounts, long devices, long transacts) {
        public int accountsAsInt() {
            if (accounts < 2) {
                throw new IllegalStateException("graph summary reports < 2 accounts: " + accounts);
            }
            if (accounts > Integer.MAX_VALUE) {
                throw new IllegalStateException("account count exceeds int max: " + accounts);
            }
            return (int) accounts;
        }
    }

    public static Counts fetch(String host, int port) throws Exception {
        Cluster cluster = Cluster.build()
                .addContactPoint(host)
                .port(port)
                .maxConnectionPoolSize(4)
                .create();
        Client client = cluster.connect();
        try {
            List<Result> results = client.submit(SUMMARY_GREMLIN).all().get(120, TimeUnit.SECONDS);
            if (results.isEmpty() || results.get(0).getObject() == null) {
                throw new IllegalStateException("empty graph summary from " + host + ":" + port);
            }
            Counts counts = parse(results.get(0).getObject());
            log.info("graph summary: {} users, {} accounts, {} devices, {} TRANSACTS edges",
                    counts.users(), counts.accounts(), counts.devices(), counts.transacts());
            return counts;
        } finally {
            client.close();
            cluster.close();
        }
    }

    @SuppressWarnings("unchecked")
    static Counts parse(Object raw) {
        Object summaryObj = raw;
        if (raw instanceof List<?> list && !list.isEmpty()) {
            summaryObj = list.get(0);
        }
        if (!(summaryObj instanceof Map<?, ?> top)) {
            throw new IllegalStateException("unexpected graph summary type: " + raw);
        }
        Map<String, Object> summary = normalizeMap(top);
        Map<String, Object> vertices = labelMap(summary, "Vertex count by label");
        Map<String, Object> edges = labelMap(summary, "Edge count by label");
        return new Counts(
                countLabel(vertices, "user"),
                countLabel(vertices, "account"),
                countLabel(vertices, "device"),
                countLabel(edges, "TRANSACTS"));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> normalizeMap(Map<?, ?> map) {
        Map<String, Object> out = new java.util.LinkedHashMap<>();
        for (Map.Entry<?, ?> e : map.entrySet()) {
            out.put(String.valueOf(e.getKey()), e.getValue());
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> labelMap(Map<String, Object> summary, String key) {
        for (Map.Entry<String, Object> entry : summary.entrySet()) {
            if (entry.getKey().equalsIgnoreCase(key)) {
                Object value = entry.getValue();
                if (value instanceof Map<?, ?> map) {
                    return normalizeMap(map);
                }
            }
        }
        return Map.of();
    }

    private static long countLabel(Map<String, Object> labels, String name) {
        for (Map.Entry<String, Object> entry : labels.entrySet()) {
            if (entry.getKey().equalsIgnoreCase(name)) {
                return toLong(entry.getValue());
            }
        }
        return 0L;
    }

    private static long toLong(Object value) {
        if (value instanceof Number n) {
            return n.longValue();
        }
        if (value instanceof String s && !s.isBlank()) {
            return Long.parseLong(s.trim());
        }
        return 0L;
    }
}
