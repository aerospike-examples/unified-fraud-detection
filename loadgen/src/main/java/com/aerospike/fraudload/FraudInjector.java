package com.aerospike.fraudload;

import com.aerospike.client.AerospikeClient;
import com.aerospike.client.Bin;
import com.aerospike.client.Key;
import com.aerospike.client.Operation;
import com.aerospike.client.Value;
import com.aerospike.client.cdt.ListOperation;
import com.aerospike.client.policy.WritePolicy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;

/**
 * Populates the small, directly-readable KV structures that let the frontend
 * find injected fraud WITHOUT scanning the billion-row dataset:
 *
 *   - flagged_accounts (keyed by user_id): the durable review queue the app
 *     already reads. Bins use the same shortened names the Python service writes
 *     (Aerospike's 15-char limit), so the backend expands them transparently.
 *
 *   - fraud_feed (single record): a short "recently flagged" update queue the
 *     frontend polls to detect a new injection run and refresh the queue.
 *
 * Account -> user mapping is deterministic (Account{n} -> User{n}, configurable
 * prefixes) with a graph in('OWNS') fallback for non-matching id schemes.
 */
public final class FraudInjector {
    private static final Logger log = LoggerFactory.getLogger(FraudInjector.class);

    public static final String FLAGGED_SET = "flagged_accounts";
    public static final String FEED_SET = "fraud_feed";
    public static final String FEED_KEY = "fraud_feed";
    private static final int RECENT_CAP = 100;

    private final AerospikeClient client;
    private final String namespace;
    private final GraphWriter graph; // nullable; used only for OWNS fallback
    private final String accountPrefix;
    private final String userPrefix;
    private final WritePolicy writePolicy;
    private final Random rnd = new Random(20260703L);
    private final List<String> flaggedUserIds = new ArrayList<>();

    public FraudInjector(AerospikeClient client, String namespace, GraphWriter graph,
                         String accountPrefix, String userPrefix) {
        this.client = client;
        this.namespace = namespace;
        this.graph = graph;
        this.accountPrefix = accountPrefix;
        this.userPrefix = userPrefix;
        this.writePolicy = new WritePolicy(client.writePolicyDefault);
    }

    /** Writes flagged_accounts + fraud_feed for the whole cohort. Returns count written. */
    public int injectFlags(FraudCohort cohort) {
        if (cohort.isEmpty()) {
            return 0;
        }
        String runId = Instant.now().toString();
        flaggedUserIds.clear();
        resetFeed(runId);

        int written = 0;
        for (String acct : cohort.mules()) {
            written += flagOne(acct, "money_mule", "Concentrated fan-in from many senders", written);
        }
        for (String acct : cohort.fraudsters()) {
            written += flagOne(acct, "fraudster", "Rapid fan-out of high-value transfers", written);
        }
        writeFeedUserIndex();
        log.info("fraud injection: wrote {} flagged_accounts (+fraud_feed run {})", written, runId);
        return written;
    }

    private int flagOne(String accountId, String typology, String reason, int writtenSoFar) {
        String userId = resolveUser(accountId);
        if (userId == null) {
            log.warn("could not map account {} to a user; skipping flag", accountId);
            return 0;
        }
        double risk = Math.round((72.0 + rnd.nextDouble() * 27.0) * 100.0) / 100.0;
        double amount = Math.round((5_000.0 + rnd.nextDouble() * 145_000.0) * 100.0) / 100.0;
        String now = Instant.now().toString();

        // account_predictions: single-element list of {account_id, risk_score}
        List<Object> preds = new ArrayList<>();
        Map<String, Object> pred = new LinkedHashMap<>();
        pred.put("account_id", accountId);
        pred.put("risk_score", risk);
        preds.add(pred);

        // Bin names MUST match Python _shorten_bin_names output:
        //   account_holder -> acct_holder, total_flagged_amount -> total_flag_amt,
        //   account_predictions -> acct_preds. Others pass through unchanged.
        Key key = new Key(namespace, FLAGGED_SET, userId);
        client.put(writePolicy, key,
                new Bin("account_id", accountId),
                new Bin("user_id", userId),
                new Bin("acct_holder", userId),
                new Bin("risk_score", risk),
                new Bin("status", "pending_review"),
                new Bin("flag_reason", reason),
                new Bin("reason", reason),
                new Bin("typology", typology),
                new Bin("flagged_date", now),
                new Bin("total_flag_amt", amount),
                new Bin("acct_preds", Value.get(preds)),
                new Bin("model_version", "loadgen-fraud-v1"),
                new Bin("confidence", 0.9),
                new Bin("source", "loadgen"),
                new Bin("created_at", now));

        // Always bump the total; only keep the first RECENT_CAP entries in the
        // preview list to keep the single feed record small for large cohorts.
        appendFeed(userId, accountId, risk, reason, now, writtenSoFar < RECENT_CAP);
        flaggedUserIds.add(userId);
        return 1;
    }

    private String resolveUser(String accountId) {
        if (accountId != null && accountId.startsWith(accountPrefix)) {
            String suffix = accountId.substring(accountPrefix.length());
            if (!suffix.isEmpty()) {
                return userPrefix + suffix;
            }
        }
        // Fallback: resolve via the graph if a client is available.
        return graph != null ? graph.resolveOwner(accountId) : null;
    }

    private void writeFeedUserIndex() {
        if (flaggedUserIds.isEmpty()) {
            return;
        }
        Key key = new Key(namespace, FEED_SET, FEED_KEY);
        client.operate(writePolicy, key,
                Operation.put(new Bin("user_ids", Value.get(new ArrayList<>(flaggedUserIds)))));
    }

    private void resetFeed(String runId) {
        String now = Instant.now().toString();
        Key key = new Key(namespace, FEED_SET, FEED_KEY);
        client.put(writePolicy, key,
                new Bin("total", 0),
                new Bin("recent", Value.get(new ArrayList<>())),
                new Bin("user_ids", Value.get(new ArrayList<>())),
                new Bin("run_id", runId),
                new Bin("run_started", now),
                new Bin("last_updated", now));
    }

    private void appendFeed(String userId, String accountId, double risk, String reason,
                            String ts, boolean keepInPreview) {
        Key key = new Key(namespace, FEED_SET, FEED_KEY);
        try {
            if (keepInPreview) {
                Map<String, Object> entry = new LinkedHashMap<>();
                entry.put("user_id", userId);
                entry.put("account_id", accountId);
                entry.put("risk_score", risk);
                entry.put("reason", reason);
                entry.put("ts", ts);
                client.operate(writePolicy, key,
                        Operation.add(new Bin("total", 1)),
                        ListOperation.append("recent", Value.get(entry)),
                        Operation.put(new Bin("last_updated", ts)));
            } else {
                client.operate(writePolicy, key,
                        Operation.add(new Bin("total", 1)),
                        Operation.put(new Bin("last_updated", ts)));
            }
        } catch (Exception e) {
            log.debug("feed append failed for {}: {}", userId, e.toString());
        }
    }
}
