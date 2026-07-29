package com.aerospike.fraudload;

import org.apache.tinkerpop.gremlin.driver.Client;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/** Minimal TRANSACTS edge writer — one addE per transaction, no OWNS/USES lookups. */
public final class GraphWriter {
    private static final Logger log = LoggerFactory.getLogger(GraphWriter.class);

    private static final String ADD_EDGE =
            "g.V(sender).addE('TRANSACTS').to(__.V(receiver))"
                    + ".property('txn_id', txnId)"
                    + ".property('amount', amount)"
                    + ".property('currency', currency)"
                    + ".property('type', type)"
                    + ".property('method', method)"
                    + ".property('location', location)"
                    + ".property('timestamp', timestamp)"
                    + ".property('status', status)"
                    + ".property('is_fraud', isFraud)"
                    + ".property('fraud_score', fraudScore)"
                    + ".property('gen_type', genType)";

    private final GremlinPool pool;

    public GraphWriter(GremlinPool pool) {
        this.pool = pool;
        log.info("graph writer bound to shared Gremlin pool");
    }

    /** Resolves the owning user id for an account via in('OWNS'); null if none. */
    public String resolveOwner(String accountId) {
        try {
            Map<String, Object> b = new HashMap<>();
            b.put("acct", accountId);
            List<org.apache.tinkerpop.gremlin.driver.Result> rs =
                    pool.sharedClient()
                            .submit("g.V(acct).in('OWNS').id().limit(1)", b)
                            .all()
                            .get(30, TimeUnit.SECONDS);
            return rs.isEmpty() ? null : String.valueOf(rs.get(0).getObject());
        } catch (Exception e) {
            log.debug("resolveOwner failed for {}: {}", accountId, e.toString());
            return null;
        }
    }

    public void writeEdge(Transaction t, int workerIndex) throws Exception {
        Client client = pool.clientForWorker(workerIndex);
        Map<String, Object> bindings = new HashMap<>();
        bindings.put("sender", t.senderAccountId());
        bindings.put("receiver", t.receiverAccountId());
        bindings.put("txnId", t.txnId());
        bindings.put("amount", t.amountCents() / 100.0);
        bindings.put("currency", "USD");
        bindings.put("type", t.type());
        bindings.put("method", "electronic_transfer");
        bindings.put("location", t.location());
        bindings.put("timestamp", t.timestamp().toString());
        bindings.put("status", t.fraud() ? "flagged" : "completed");
        bindings.put("isFraud", t.fraud());
        bindings.put("fraudScore", t.fraudScore());
        bindings.put("genType", t.fraud() ? "LOADGEN_FRAUD" : "LOADGEN");

        client.submit(ADD_EDGE, bindings).all().get(30, TimeUnit.SECONDS);
        // AGS often returns an empty result set for mutating traversals; that is still success.
    }
}
