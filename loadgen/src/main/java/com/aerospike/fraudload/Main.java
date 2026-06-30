package com.aerospike.fraudload;

import picocli.CommandLine;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import java.util.concurrent.Callable;

@Command(name = "fraud-loadgen", mixinStandardHelpOptions = true,
        description = "High-throughput fraud transaction load generator for Aerospike KV.")
public final class Main implements Callable<Integer> {
    @Option(names = "--host", defaultValue = "localhost") String host;
    @Option(names = "--port", defaultValue = "3000") int port;
    @Option(names = "--namespace", defaultValue = "test") String namespace;
    @Option(names = "--accounts", defaultValue = "1000000") int accounts;
    @Option(names = "--workers", defaultValue = "16") int workers;
    @Option(names = "--rate", defaultValue = "0", description = "target txn/s, 0 = unbounded") long rate;
    @Option(names = "--duration", defaultValue = "30") int duration;
    @Option(names = "--balances", defaultValue = "true") boolean balances;

    @Override
    public Integer call() throws Exception {
        Config cfg = new Config(host, port, namespace, accounts, workers, rate, duration, balances);
        new LoadDriver(cfg).run();
        return 0;
    }

    public static void main(String[] args) {
        System.exit(new CommandLine(new Main()).execute(args));
    }
}
