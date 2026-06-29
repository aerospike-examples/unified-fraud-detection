"""Metrics collector unit and concurrency tests."""

import threading
import time

import pytest

from workflow.metrics import MetricsCollector, get_collector, remove_collector


class TestMetricsCollector:
    def test_node_timing(self):
        c = MetricsCollector()
        c.start_node("alert_validation")
        time.sleep(0.01)
        c.end_node("alert_validation")
        m = c.get_metrics()
        assert m["node_durations"]["alert_validation"] >= 5

    def test_db_and_llm_aggregation(self):
        c = MetricsCollector()
        c.track_db_call("get_user", "KV", 10.0)
        c.track_db_call("gremlin", "Graph", 20.0)
        c.track_llm_call(100.0, tokens_in=50, tokens_out=25)
        m = c.get_metrics()
        assert m["kv_calls"] == 1
        assert m["graph_calls"] == 1
        assert m["llm_calls"] == 1
        assert m["llm_tokens_in"] == 50

    def test_tool_breakdown(self):
        c = MetricsCollector()
        c.track_tool_call("get_account_transactions")
        c.track_tool_call("get_account_transactions")
        c.track_tool_call("detect_fraud_ring")
        m = c.get_metrics()
        assert m["tool_calls_count"] == 3
        assert m["tool_breakdown"]["get_account_transactions"] == 2


@pytest.mark.stress
def test_collector_registry_concurrent():
    """Many threads creating/removing collectors should not corrupt the registry."""
    errors = []

    def worker(i: int):
        try:
            inv_id = f"inv_stress_{i}"
            c = get_collector(inv_id)
            c.track_db_call("ping", "KV", 1.0)
            remove_collector(inv_id)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
