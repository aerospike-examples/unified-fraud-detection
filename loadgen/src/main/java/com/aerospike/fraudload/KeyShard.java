package com.aerospike.fraudload;

/** Splits the contiguous account index space [0, accountCount) across workers. */
public final class KeyShard {
    private final int firstIndex;
    private final int endIndex;

    public KeyShard(int workerIndex, int workers, int accountCount) {
        int base = accountCount / workers;
        int remainder = accountCount % workers;
        int start = workerIndex * base + Math.min(workerIndex, remainder);
        int size = base + (workerIndex < remainder ? 1 : 0);
        this.firstIndex = start;
        this.endIndex = start + size;
    }

    public int firstIndex() { return firstIndex; }
    public int endIndex() { return endIndex; }
    public int size() { return endIndex - firstIndex; }
}
