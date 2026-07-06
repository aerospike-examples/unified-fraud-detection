"""Tests for deterministic report generation."""

from tests.fixtures import SAMPLE_ALERT, SAMPLE_INITIAL
from workflow.assessment import deterministic_assessment
from workflow.nodes.report_generation import generate_fallback_report


def _report_state():
    assessment = deterministic_assessment(SAMPLE_INITIAL, SAMPLE_ALERT)
    return {
        "user_id": SAMPLE_INITIAL["user_id"],
        "investigation_id": "inv_test_report",
        "started_at": "2026-06-29T12:00:00",
        "alert_evidence": SAMPLE_ALERT,
        "initial_evidence": SAMPLE_INITIAL,
        "final_assessment": assessment,
        "tool_calls": [{"tool": "get_account_transactions", "params": {}, "result": {}}],
        "agent_iterations": 1,
        "network_findings": "- mock network finding",
        "device_findings": "- mock device finding",
        "velocity_findings": "- mock velocity finding",
    }


class TestFallbackReport:
    def test_produces_markdown_sections(self):
        md = generate_fallback_report(_report_state())
        assert "# " in md or "## " in md
        assert "inv_test_report" in md or "U0001234" in md

    def test_includes_assessment_fields(self):
        assessment = deterministic_assessment(SAMPLE_INITIAL, SAMPLE_ALERT)
        md = generate_fallback_report(_report_state())
        assert assessment["typology"] in md or assessment["decision"] in md

    def test_handles_missing_assessment(self):
        state = _report_state()
        state["final_assessment"] = {}
        md = generate_fallback_report(state)
        assert isinstance(md, str) and len(md) > 50

    def test_fraud_ring_mermaid_when_ring_detected(self):
        state = _report_state()
        state["tool_calls"] = [{
            "tool": "detect_fraud_ring",
            "params": {"user_id": "U0001234"},
            "result": {
                "success": True,
                "is_fraud_ring": True,
                "ring_confidence": 0.9,
                "ring_members": [
                    {"user_id": "U0009999", "name": "Ring Member", "risk_score": 80},
                ],
                "potential_ring": {
                    "cluster_density": 0.5,
                    "triangle_count": 2,
                    "reciprocal_partner_count": 1,
                    "high_volume_pair_count": 1,
                    "high_volume_pairs": [{"user_id": "U0009999", "transaction_count": 60}],
                    "cluster_members": ["U0009999"],
                    "triangles": [{"members": ["U0001234", "U0009999", "U0008888"]}],
                },
                "evidence": ["dense cluster"],
            },
        }]
        md = generate_fallback_report(state)
        assert "mermaid" in md.lower() or "Fraud Ring" in md
