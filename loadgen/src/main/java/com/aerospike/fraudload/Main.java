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
    @Option(names = "--accounts", defaultValue = "1000000",
            description = "Account count (synthetic) or cap when loading --accounts-file") int accounts;
    @Option(names = "--accounts-file",
            description = "Graph accounts CSV (~id column); required for graph writes against seeded data") String accountsFile;
    @Option(names = "--workers", defaultValue = "16", description = "Worker threads") int workers;
    @Option(names = "--rate", defaultValue = "0", description = "Target txn/s, 0 = unbounded") long rate;
    @Option(names = "--duration", defaultValue = "30", description = "Run duration in seconds") int duration;
    @Option(names = "--balances", defaultValue = "true", description = "Atomic balance increments in KV") boolean balances;
    @Option(names = "--mode", defaultValue = "kv",
            description = "Write path: kv, graph, or paired (parallel KV + graph)") WriteMode mode;
    @Option(names = "--graph-host", defaultValue = "localhost", description = "Gremlin/AGS host") String graphHost;
    @Option(names = "--graph-port", defaultValue = "8182", description = "Gremlin/AGS port") int graphPort;
    @Option(names = "--mules", defaultValue = "0",
            description = "Fraud cohort: accounts receiving concentrated fan-in (money mules)") int mules;
    @Option(names = "--fraudsters", defaultValue = "0",
            description = "Fraud cohort: accounts originating fan-out/bursts") int fraudsters;
    @Option(names = "--fraud-ratio", defaultValue = "0.0",
            description = "Fraction (0..1) of generated txns that are fraudulent (needs a cohort)") double fraudRatio;
    @Option(names = "--account-prefix", defaultValue = "Account",
            description = "Account id prefix for deterministic account->user mapping") String accountPrefix;
    @Option(names = "--user-prefix", defaultValue = "User",
            description = "User id prefix for deterministic account->user mapping") String userPrefix;

    @Override
    public Integer call() throws Exception {
        AccountPool pool = resolveAccountPool();
        FraudCohort cohort = FraudCohort.select(pool, mules, fraudsters);
        double effectiveFraudRatio = fraudRatio;
        if (!cohort.isEmpty() && fraudRatio <= 0.0) {
            effectiveFraudRatio = 0.02; // sensible default so a cohort actually gets fraud traffic
            System.err.println("INFO: cohort set but --fraud-ratio=0; defaulting to 0.02");
        }
        Config cfg = new Config(host, port, namespace, pool, workers, rate, duration, balances,
                mode, graphHost, graphPort, cohort, effectiveFraudRatio, accountPrefix, userPrefix);
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
        if (mode.writesGraph()) {
            System.err.println("WARN: graph mode without --accounts-file uses synthetic acct-N ids; "
                    + "edges fail unless those vertices exist. Pass the seeded accounts CSV for demos.");
        }
        return AccountPool.synthetic(accounts);
    }

    public static void main(String[] args) {
        System.exit(new CommandLine(new Main()).execute(args));
    }
}
