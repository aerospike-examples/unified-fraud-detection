package com.aerospike.fraudload;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/** One synthetic transaction. Amount is stored in integer cents to keep balance math atomic. */
public record Transaction(
        String txnId,
        String senderAccountId,
        String receiverAccountId,
        long amountCents,
        String type,
        String location,
        Instant timestamp,
        boolean fraud,
        double fraudScore,
        String alertAccountId,
        String fraudTypology) {

    /** Convenience constructor for benign transactions (fraud=false, score=0). */
    public Transaction(String txnId, String senderAccountId, String receiverAccountId,
                       long amountCents, String type, String location, Instant timestamp) {
        this(txnId, senderAccountId, receiverAccountId, amountCents, type, location, timestamp,
                false, 0.0, null, null);
    }

    /** The per-entry map stored under the txs map, keyed by ISO timestamp. */
    public Map<String, Object> toEntry(String direction, String counterparty) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("txn_id", txnId);
        m.put("amount", amountCents / 100.0);
        m.put("type", type);
        m.put("counterparty", counterparty);
        m.put("direction", direction);
        m.put("location", location);
        m.put("status", fraud ? "flagged" : "completed");
        m.put("is_fraud", fraud);
        m.put("fraud_score", fraudScore);
        return m;
    }
}
