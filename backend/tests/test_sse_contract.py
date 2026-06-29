"""SSE contract parity tests across engine and service layers."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from tests.helpers import collect_async, validate_engine_events, validate_service_sse


@pytest.mark.asyncio
async def test_engine_sse_contract_complete(mock_engine, low_risk_pipeline):
    events = await collect_async(mock_engine.run_investigation("U0001234", "inv_contract"))
    validate_engine_events(events, expect_complete=True)
    assert "metrics" in [e.get("type") for e in events]


@pytest.mark.asyncio
async def test_service_sse_required_trace_types(investigation_service, low_risk_pipeline):
    events = await collect_async(
        investigation_service.stream_investigation("U0001234", "inv_traces"),
    )
    trace_types = {
        e["data"]["type"]
        for e in events
        if e.get("event") == "trace"
    }
    for required in (
        "node_start",
        "node_complete",
        "specialist_finding",
        "assessment",
        "action_proposed",
    ):
        assert required in trace_types, f"missing trace type {required}"


@pytest.mark.asyncio
async def test_mock_force_hitl_env(investigation_service, low_risk_pipeline, monkeypatch):
    monkeypatch.setenv("MOCK_FORCE_HITL", "true")
    events = await collect_async(
        investigation_service.stream_investigation("U0001234", "inv_force_hitl"),
    )
    validate_service_sse(events, expect_hitl=True, engine="mock")


@pytest.mark.asyncio
async def test_metrics_emitted_on_complete(investigation_service, low_risk_pipeline):
    events = await collect_async(
        investigation_service.stream_investigation("U0001234", "inv_metrics"),
    )
    metrics_events = [e for e in events if e.get("event") == "metrics"]
    assert len(metrics_events) == 1
    payload = metrics_events[0]["data"]
    inner = payload.get("data") or payload
    assert inner.get("llm_calls") == 0
    assert inner.get("total_duration_ms", 0) >= 0
    assert "node_durations" in inner
