"""Shared test data for investigation workflow tests."""

SAMPLE_ALERT = {
    "trigger_type": "ML",
    "trigger_rule": "Pattern Anomaly Detection",
    "original_score": 85,
    "flag_reason": "Elevated risk score",
    "trigger_timestamp": "2026-06-29T12:00:00",
    "previous_flags_count": 0,
}

SAMPLE_INITIAL = {
    "user_id": "U0001234",
    "profile": {"name": "Test User", "location": "NY", "occupation": "Engineer"},
    "accounts": {
        "A000123401": {
            "type": "checking",
            "balance": 5000,
            "is_fraud": True,
            "status": "active",
        },
    },
    "devices": {"D000123401": {"type": "mobile", "os": "iOS", "is_fraud": False}},
    "account_facts": {
        "A000123401": {
            "transaction_zscore": 3.5,
            "amount_zscore_7d": 2.5,
            "new_recipient_ratio_7d": 0.6,
            "txn_out_count_7d": 42,
            "total_out_amount_7d": 12000,
            "unique_recipients_7d": 8,
        },
    },
    "device_facts": {
        "D000123401": {
            "shared_account_count_7d": 4,
            "flagged_account_count": 0,
            "avg_account_risk_score": 55,
            "max_account_risk_score": 70,
        },
    },
    "account_metrics": {
        "has_flagged_account": True,
        "has_flagged_device": False,
        "max_velocity_zscore": 3.5,
        "max_amount_zscore": 2.5,
        "max_new_recipient_ratio": 0.6,
        "max_shared_accounts_on_device": 4,
        "profile_risk_score": 85,
        "total_balance": 5000,
        "account_age_days": 120,
        "kyc_completeness": "complete",
        "flagged_account_count": 1,
        "flagged_device_count": 0,
    },
}

LOW_RISK_INITIAL = {
    **SAMPLE_INITIAL,
    "accounts": {
        "A000123401": {
            "type": "checking",
            "balance": 500,
            "is_fraud": False,
            "status": "active",
        },
    },
    "account_metrics": {
        **SAMPLE_INITIAL["account_metrics"],
        "has_flagged_account": False,
        "has_flagged_device": False,
        "max_velocity_zscore": 0.5,
        "max_amount_zscore": 0.5,
        "max_new_recipient_ratio": 0.1,
        "max_shared_accounts_on_device": 0,
        "flagged_account_count": 0,
    },
}

LOW_RISK_ALERT = {**SAMPLE_ALERT, "original_score": 30}
