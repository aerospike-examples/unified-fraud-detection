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
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Live fraud detection: flags accounts when fraudulent transactions are written.
 * Persists a durable review queue in flagged_accounts (key=user_id) plus a
 * persistent flagged_queue index for batch reads across loadgen restarts.
 */
public final class FraudInjector {
    private static final Logger log = LoggerFactory.getLogger(FraudInjector.class);

    public static final String FLAGGED_SET = "flagged_accounts";
    public static final String QUEUE_SET = "flagged_queue";
    public static final String QUEUE_KEY = "index";
    public static final String FEED_SET = "fraud_feed";
    public static final String FEED_KEY = "fraud_feed";
    private static final int RECENT_CAP = 100;
    private static final int QUEUE_CAP = 50_000;

    private final AerospikeClient client;
    private final String namespace;
    private final GraphWriter graph;
    private final String accountPrefix;
    private final String userPrefix;
    private final WritePolicy writePolicy;
    private final Random rnd = new Random(20260703L);
    /** Users flagged this JVM run — avoids duplicate KV writes under concurrency. */
    private final Set<String> flaggedUsers = ConcurrentHashMap.newKeySet();
    /** Users already in persistent queue index (loaded at beginRun). */
    private final Set<String> queuedUsers = ConcurrentHashMap.newKeySet();

    public FraudInjector(AerospikeClient client, String namespace, GraphWriter graph,
                         String accountPrefix, String userPrefix) {
        this.client = client;
        this.namespace = namespace;
        this.graph = graph;
        this.accountPrefix = accountPrefix;
        this.userPrefix = userPrefix;
        this.writePolicy = new WritePolicy(client.writePolicyDefault);
    }

    /** Reset per-run fraud_feed counters; load persistent queue index into memory. */
    public void beginRun() {
        flaggedUsers.clear();
        queuedUsers.clear();
        queuedUsers.addAll(loadQueueUserIds());

        String runId = Instant.now().toString();
        Key key = new Key(namespace, FEED_SET, FEED_KEY);
        client.put(writePolicy, key,
                new Bin("total", 0),
                new Bin("recent", Value.get(new ArrayList<>())),
                new Bin("run_id", runId),
                new Bin("run_started", runId),
                new Bin("last_updated", runId));
        log.info("live fraud detection enabled (fraud_feed run {}, {} users in flagged_queue)",
                runId, queuedUsers.size());
    }

    /**
     * Flag the alert account from a fraudulent transaction if not already flagged.
     * Thread-safe for concurrent loadgen workers.
     */
    public void flagOnDetection(Transaction txn) {
        if (!txn.fraud() || txn.alertAccountId() == null) {
            return;
        }
        String accountId = txn.alertAccountId();
        String userId = resolveUser(accountId);
        if (userId == null) {
            log.debug("could not map account {} to user; skipping live flag", accountId);
            return;
        }
        if (!flaggedUsers.add(userId)) {
            return;
        }
        String typology = txn.fraudTypology() != null ? txn.fraudTypology() : "suspicious_activity";
        String reason = reasonFor(typology);
        double risk = txn.fraudScore() > 0
                ? txn.fraudScore()
                : Math.round((72.0 + rnd.nextDouble() * 27.0) * 100.0) / 100.0;
        double amount = Math.round((txn.amountCents() / 100.0) * 100.0) / 100.0;
        String now = Instant.now().toString();

        List<Object> preds = new ArrayList<>();
        Map<String, Object> pred = new LinkedHashMap<>();
        pred.put("account_id", accountId);
        pred.put("risk_score", risk);
        preds.add(pred);

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
                new Bin("model_version", "loadgen-live-v1"),
                new Bin("confidence", 0.9),
                new Bin("source", "loadgen-live"),
                new Bin("created_at", now));

        appendQueueUserId(userId, now);
        appendFeed(userId, accountId, risk, reason, now);
    }

    private static String reasonFor(String typology) {
        return "money_mule".equals(typology)
                ? "Concentrated fan-in from many senders"
                : "Rapid fan-out of high-value transfers";
    }

    private String resolveUser(String accountId) {
        if (accountId != null && accountId.startsWith(accountPrefix)) {
            String suffix = accountId.substring(accountPrefix.length());
            if (!suffix.isEmpty()) {
                return userPrefix + suffix;
            }
        }
        return graph != null ? graph.resolveOwner(accountId) : null;
    }

    @SuppressWarnings("unchecked")
    private List<String> loadQueueUserIds() {
        List<String> ids = new ArrayList<>();
        try {
            Key key = new Key(namespace, QUEUE_SET, QUEUE_KEY);
            var rec = client.get(null, key);
            if (rec != null) {
                Object raw = rec.getValue("user_ids");
                if (raw instanceof List<?> list) {
                    for (Object o : list) {
                        if (o != null) {
                            ids.add(o.toString());
                        }
                    }
                }
            }
        } catch (Exception e) {
            log.warn("could not read flagged_queue index: {}", e.toString());
        }
        return ids;
    }

    private void appendQueueUserId(String userId, String ts) {
        if (!queuedUsers.add(userId)) {
            return;
        }
        if (queuedUsers.size() > QUEUE_CAP) {
            log.warn("flagged_queue cap {} reached; skipping index append for {}", QUEUE_CAP, userId);
            return;
        }
        Key key = new Key(namespace, QUEUE_SET, QUEUE_KEY);
        synchronized (this) {
            try {
                client.operate(writePolicy, key,
                        ListOperation.append("user_ids", Value.get(userId)),
                        Operation.add(new Bin("total", 1)),
                        Operation.put(new Bin("last_updated", ts)));
            } catch (Exception e) {
                log.debug("flagged_queue append failed for {}: {}", userId, e.toString());
            }
        }
    }

    private void appendFeed(String userId, String accountId, double risk, String reason, String ts) {
        Key key = new Key(namespace, FEED_SET, FEED_KEY);
        synchronized (this) {
            try {
                Map<String, Object> entry = new LinkedHashMap<>();
                entry.put("user_id", userId);
                entry.put("account_id", accountId);
                entry.put("risk_score", risk);
                entry.put("reason", reason);
                entry.put("ts", ts);

                int runTotal = flaggedUsers.size();
                boolean keepInPreview = runTotal <= RECENT_CAP;

                if (keepInPreview) {
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
}
