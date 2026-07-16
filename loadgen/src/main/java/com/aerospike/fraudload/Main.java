package com.aerospike.fraudload;

import picocli.CommandLine;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.Callable;

@Command(name = "fraud-loadgen", mixinStandardHelpOptions = true,
        description = "High-throughput fraud transaction load generator (KV + optional graph dual-write).")
public final class Main implements Callable<Integer> {
    @Option(names = "--host", defaultValue = "localhost", description = "Aerospike host") String host;
    @Option(names = "--port", defaultValue = "3000", description = "Aerospike port") int port;
    @Option(names = "--namespace", defaultValue = "test", description = "Aerospike namespace") String namespace;
    @Option(names = "--accounts", defaultValue = "0",
            description = "Account pool size (Account1..N); 0 = read account count from graph summary") int accounts;
    @Option(names = "--accounts-file",
            description = "Optional graph accounts CSV (~id column); default derives Account1..N from --account-prefix") String accountsFile;
    @Option(names = "--workers", defaultValue = "16", description = "Worker threads") int workers;
    @Option(names = "--rate", defaultValue = "0", description = "Target txn/s, 0 = unbounded") long rate;
    @Option(names = "--duration", defaultValue = "30", description = "Run duration in seconds") int duration;
    @Option(names = "--balances", defaultValue = "true", description = "Atomic balance increments in KV") boolean balances;
    @Option(names = "--mode", defaultValue = "kv",
            description = "Write path: kv, graph, or paired (parallel KV + graph)") WriteMode mode;
    @Option(names = "--graph-host", defaultValue = "localhost", description = "Gremlin/AGS host") String graphHost;
    @Option(names = "--graph-port", defaultValue = "8182", description = "Gremlin/AGS port") int graphPort;
    @Option(names = "--mules", defaultValue = "0", hidden = true,
            description = "Deprecated — live fraud uses --fraud-ratio only") int mules;
    @Option(names = "--fraudsters", defaultValue = "0", hidden = true,
            description = "Deprecated — live fraud uses --fraud-ratio only") int fraudsters;
    @Option(names = "--fraud-ratio", defaultValue = "0.0",
            description = "Fraction (0..1) of txns flagged as fraudulent; accounts flagged live on detection") double fraudRatio;
    @Option(names = "--account-prefix", defaultValue = "Account",
            description = "Account id prefix for deterministic account->user mapping") String accountPrefix;
    @Option(names = "--user-prefix", defaultValue = "User",
            description = "User id prefix for deterministic account->user mapping") String userPrefix;
    @Option(names = "--cohort-seed", defaultValue = "0", hidden = true,
            description = "Deprecated — no fixed cohort") long cohortSeed;
    @Option(names = "--ring-pool-size", defaultValue = "12",
            description = "Per-worker rotating cohort size that ring-mode fraud txns are drawn from; "
                    + "0 disables ring bias entirely") int ringPoolSize;
    @Option(names = "--ring-ratio", defaultValue = "0.4",
            description = "Fraction (0..1) of fraud txns biased towards the ring pool (both sender and "
                    + "receiver), vs. fully random sender/receiver anywhere in the shard") double ringRatio;

    @Override
    public Integer call() throws Exception {
        if (mules > 0 || fraudsters > 0) {
            System.err.println("WARN: --mules/--fraudsters are deprecated; "
                    + "fraud is injected live via --fraud-ratio on random accounts");
        }
        AccountPool pool = resolveAccountPool();
        Config cfg = new Config(host, port, namespace, pool, workers, rate, duration, balances,
                mode, graphHost, graphPort, fraudRatio, accountPrefix, userPrefix, ringPoolSize, ringRatio);
        new LoadDriver(cfg).run();
        return 0;
    }

    private AccountPool resolveAccountPool() throws Exception {
        if (accountsFile != null && !accountsFile.isBlank()) {
            Path path = Path.of(accountsFile);
            if (!Files.isRegularFile(path)) {
                throw new CommandLine.ParameterException(new CommandLine(this),
                        "accounts file not found: " + path);
            }
            return AccountPool.fromCsv(path, accounts);
        }

        int poolSize = accounts;
        if (poolSize <= 0) {
            if (!mode.writesGraph()) {
                throw new CommandLine.ParameterException(new CommandLine(this),
                        "--accounts 0 requires graph or paired mode to read the graph summary");
            }
            GraphSummary.Counts summary = GraphSummary.fetch(graphHost, graphPort);
            poolSize = summary.accountsAsInt();
            System.err.printf(
                    "INFO: account pool from graph summary: %,d accounts (%,d users, %,d TRANSACTS edges)%n",
                    summary.accounts(), summary.users(), summary.transacts());
        }
        return AccountPool.deterministic(poolSize, accountPrefix);
    }

    public static void main(String[] args) {
        System.exit(new CommandLine(new Main()).execute(args));
    }
}
