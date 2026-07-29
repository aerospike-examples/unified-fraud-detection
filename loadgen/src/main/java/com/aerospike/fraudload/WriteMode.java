package com.aerospike.fraudload;

/** Which stores the load harness writes to. */
public enum WriteMode {
    kv,
    graph,
    paired;

    public boolean writesKv() {
        return this != graph;
    }

    public boolean writesGraph() {
        return this != kv;
    }
}
