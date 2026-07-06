package com.aerospike.fraudload;

import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class GraphSummaryTest {
    @Test
    void parsesAgsSummaryShape() {
        Map<String, Object> vertices = new LinkedHashMap<>();
        vertices.put("user", 1_000_000_000L);
        vertices.put("account", 1_000_000_000L);
        vertices.put("device", 1_000_000_000L);

        Map<String, Object> edges = new LinkedHashMap<>();
        edges.put("TRANSACTS", 3_000_000_000L);

        Map<String, Object> raw = new LinkedHashMap<>();
        raw.put("Total vertex count", 3_000_000_000L);
        raw.put("Vertex count by label", vertices);
        raw.put("Total edge count", 3_000_000_000L);
        raw.put("Edge count by label", edges);

        GraphSummary.Counts counts = GraphSummary.parse(raw);
        assertEquals(1_000_000_000L, counts.users());
        assertEquals(1_000_000_000L, counts.accounts());
        assertEquals(1_000_000_000L, counts.devices());
        assertEquals(3_000_000_000L, counts.transacts());
        assertEquals(1_000_000_000, counts.accountsAsInt());
    }

    @Test
    void labelLookupIsCaseInsensitive() {
        Map<String, Object> vertices = Map.of("Account", 42);
        Map<String, Object> raw = Map.of("Vertex count by label", vertices);
        assertEquals(42L, GraphSummary.parse(raw).accounts());
    }
}
