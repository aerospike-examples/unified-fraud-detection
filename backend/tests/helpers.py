"""Test helpers for async generators and SSE contract validation."""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional, Set


async def collect_async(agen: AsyncIterator[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    async for ev in agen:
        events.append(ev)
    return events


def event_types(events: List[Dict[str, Any]], key: str = "type") -> List[str]:
    return [e.get(key, "") for e in events]


def service_event_types(events: List[Dict[str, Any]]) -> List[str]:
    return event_types(events, key="event")


REQUIRED_START_FIELDS = {"investigation_id", "user_id", "steps", "engine"}
REQUIRED_TRACE_FIELDS = {"type", "node", "timestamp", "data"}
REQUIRED_COMPLETE_PHASES = (
    "alert_validation",
    "data_collection",
    "llm_agent",
    "report_generation",
)


def validate_engine_events(events: List[Dict[str, Any]], *, expect_complete: bool = True) -> None:
    """Assert the low-level engine SSE dict contract."""
    types = event_types(events)
    if expect_complete:
        assert "complete" in types, f"expected complete, got {types}"
        assert "error" not in types, f"unexpected error in {types}"
    assert "state_update" in types or "action_confirmation_required" in types

    for ev in events:
        if ev.get("type") == "trace":
            trace = ev.get("event") or {}
            missing = REQUIRED_TRACE_FIELDS - set(trace.keys())
            assert not missing, f"trace missing fields {missing}: {trace}"


def validate_service_sse(
    events: List[Dict[str, Any]],
    *,
    expect_complete: bool = True,
    expect_hitl: bool = False,
    engine: str = "mock",
    require_all_nodes: bool = True,
) -> Dict[str, Any]:
    """Assert InvestigationService → API SSE shape and return a summary."""
    types = service_event_types(events)
    assert types, "no SSE events"
    assert types[0] == "start", types

    start = events[0].get("data") or {}
    missing = REQUIRED_START_FIELDS - set(start.keys())
    assert not missing, f"start event missing {missing}"

    assert start.get("engine") == engine
    assert len(start.get("steps") or []) == 4

    summary: Dict[str, Any] = {
        "investigation_id": start.get("investigation_id"),
        "event_count": len(events),
        "event_types": sorted(set(types)),
        "trace_types": [],
        "has_assessment": False,
        "has_report": False,
        "hitl_pause": "action_confirmation_required" in types,
        "complete": "complete" in types,
        "error": None,
    }

    for ev in events:
        et = ev.get("event")
        data = ev.get("data") or {}
        if et == "trace":
            summary["trace_types"].append(data.get("type"))
        elif et == "progress":
            if data.get("final_assessment"):
                summary["has_assessment"] = True
            if data.get("report_markdown"):
                summary["has_report"] = True
        elif et == "error":
            summary["error"] = data.get("error")
        elif et == "metrics":
            summary["metrics"] = data.get("data") or data

    if expect_hitl:
        assert summary["hitl_pause"], f"expected HITL pause, types={types}"
        assert not summary["complete"], "HITL run should not complete until resume"
    elif expect_complete:
        assert summary["complete"], f"expected complete, types={types}"
        assert summary["has_assessment"], "completed run should surface assessment"
        assert summary["has_report"], "completed run should surface report"
        assert summary["metrics"] is not None, "completed run should emit metrics"
        assert summary["metrics"].get("llm_calls", -1) == 0, "mock engine must not call LLM"

    node_starts: Set[str] = {
        e["data"]["node"]
        for e in events
        if e.get("event") == "trace" and (e.get("data") or {}).get("type") == "node_start"
    }
    if require_all_nodes:
        for phase in REQUIRED_COMPLETE_PHASES:
            if expect_complete or (expect_hitl and phase != "report_generation"):
                assert phase in node_starts, f"missing node_start for {phase}, got {node_starts}"

    return summary


def patch_mock_pipeline(
    mock_alert,
    mock_data,
    *,
    alert=None,
    initial=None,
):
    """Wire alert/data collection mocks with shared fixtures."""
    mock_alert.return_value = {
        "alert_evidence": alert or {},
        "trace_events": [],
    }
    mock_data.return_value = {
        "initial_evidence": initial or {},
        "trace_events": [],
    }
