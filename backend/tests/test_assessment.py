"""Tests for deterministic assessment and evidence summary."""

import pytest

from tests.fixtures import LOW_RISK_ALERT, LOW_RISK_INITIAL, SAMPLE_ALERT, SAMPLE_INITIAL
from workflow.assessment import build_evidence_summary, deterministic_assessment
from workflow.action_core import DESTRUCTIVE_DECISIONS, NONDESTRUCTIVE_DECISIONS


class TestDeterministicAssessment:
    def test_high_risk_freeze(self):
        result = deterministic_assessment(SAMPLE_INITIAL, SAMPLE_ALERT)
        assert result["risk_score"] >= 80
        assert result["risk_level"] == "critical"
        assert result["decision"] in DESTRUCTIVE_DECISIONS
        assert result["decision"] == "temporary_freeze"
        assert result["typology"] in ("fraud_ring", "money_mule", "suspicious_activity")

    def test_low_risk_monitor(self):
        result = deterministic_assessment(LOW_RISK_INITIAL, LOW_RISK_ALERT)
        assert result["risk_score"] < 60
        assert result["decision"] in NONDESTRUCTIVE_DECISIONS
        assert result["decision"] == "allow_monitor"

    def test_step_up_auth_mid_band(self):
        initial = {
            **LOW_RISK_INITIAL,
            "account_metrics": {
                **LOW_RISK_INITIAL["account_metrics"],
                "max_velocity_zscore": 2.5,
                "max_amount_zscore": 2.5,
            },
        }
        alert = {**LOW_RISK_ALERT, "original_score": 55}
        result = deterministic_assessment(initial, alert)
        assert 60 <= result["risk_score"] < 80
        assert result["decision"] == "step_up_auth"

    def test_money_mule_typology(self):
        initial = {
            **SAMPLE_INITIAL,
            "account_metrics": {
                **SAMPLE_INITIAL["account_metrics"],
                "max_velocity_zscore": 3.0,
                "max_new_recipient_ratio": 0.8,
                "max_shared_accounts_on_device": 0,
                "has_flagged_account": False,
            },
        }
        result = deterministic_assessment(initial, SAMPLE_ALERT)
        assert result["typology"] == "money_mule"

    def test_fraud_ring_typology(self):
        initial = {
            **SAMPLE_INITIAL,
            "account_metrics": {
                **SAMPLE_INITIAL["account_metrics"],
                "max_shared_accounts_on_device": 5,
                "max_velocity_zscore": 1.0,
                "max_new_recipient_ratio": 0.1,
            },
        }
        result = deterministic_assessment(initial, SAMPLE_ALERT)
        assert result["typology"] == "fraud_ring"

    def test_risk_score_capped_at_100(self):
        initial = {
            **SAMPLE_INITIAL,
            "account_metrics": {
                **SAMPLE_INITIAL["account_metrics"],
                "has_flagged_account": True,
                "has_flagged_device": True,
                "max_velocity_zscore": 5.0,
                "max_amount_zscore": 5.0,
                "max_new_recipient_ratio": 0.9,
                "max_shared_accounts_on_device": 10,
            },
        }
        alert = {**SAMPLE_ALERT, "original_score": 95}
        result = deterministic_assessment(initial, alert)
        assert result["risk_score"] == 100

    def test_reasoning_includes_signals(self):
        result = deterministic_assessment(SAMPLE_INITIAL, SAMPLE_ALERT)
        assert "Velocity z-score" in result["reasoning"]
        assert "original score" in result["reasoning"].lower()


class TestBuildEvidenceSummary:
    def test_includes_user_and_alert(self):
        text = build_evidence_summary(SAMPLE_INITIAL, SAMPLE_ALERT)
        assert "U0001234" in text
        assert "Test User" in text
        assert "Pattern Anomaly" in text or "ML" in text

    def test_flags_fraud_account(self):
        text = build_evidence_summary(SAMPLE_INITIAL, SAMPLE_ALERT)
        assert "FLAGGED FRAUD" in text

    def test_velocity_anomaly_marker(self):
        text = build_evidence_summary(SAMPLE_INITIAL, SAMPLE_ALERT)
        assert "VELOCITY ANOMALY" in text

    def test_empty_initial_does_not_crash(self):
        text = build_evidence_summary({}, {})
        assert "INVESTIGATION EVIDENCE" in text
