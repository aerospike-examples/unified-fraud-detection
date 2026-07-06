"""InvestigationService integration tests (mock engine, no docker)."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from services.investigation_service import InvestigationService
from tests.fixtures import LOW_RISK_ALERT, LOW_RISK_INITIAL
from tests.helpers import collect_async, service_event_types, validate_service_sse


@pytest.mark.asyncio
async def test_stream_complete_low_risk(investigation_service, low_risk_pipeline):
    events = await collect_async(
        investigation_service.stream_investigation("U0001234", "inv_svc_low"),
    )
    summary = validate_service_sse(events, expect_complete=True, engine="mock")
    assert summary["investigation_id"] == "inv_svc_low"
    assert "assessment" in summary["trace_types"]


@pytest.mark.asyncio
async def test_stream_hitl_high_risk(investigation_service, high_risk_pipeline):
    events = await collect_async(
        investigation_service.stream_investigation("U0001234", "inv_svc_hitl"),
    )
    validate_service_sse(events, expect_complete=False, expect_hitl=True, engine="mock")
    assert investigation_service.has_pending_action("inv_svc_hitl")


@pytest.mark.asyncio
async def test_resume_approve_completes(investigation_service, high_risk_pipeline):
    await collect_async(
        investigation_service.stream_investigation("U0001234", "inv_svc_resume"),
    )
    assert investigation_service.has_pending_action("inv_svc_resume")

    resume_events = await collect_async(
        investigation_service.resume_investigation_action("inv_svc_resume", approved=True),
    )
    types = service_event_types(resume_events)
    assert "complete" in types
    assert not investigation_service.has_pending_action("inv_svc_resume")

    result = investigation_service.get_investigation_result("inv_svc_resume")
    assert result is not None
    assert result["state"].get("final_assessment")
    assert result["state"].get("report_markdown")


@pytest.mark.asyncio
async def test_resume_reject_with_override(investigation_service, high_risk_pipeline):
    await collect_async(
        investigation_service.stream_investigation("U0001234", "inv_svc_override"),
    )

    resume_events = await collect_async(
        investigation_service.resume_investigation_action(
            "inv_svc_override",
            approved=False,
            override="allow_monitor",
        ),
    )
    assert "complete" in service_event_types(resume_events)
    result = investigation_service.get_investigation_result("inv_svc_override")
    enacted = (result or {}).get("state", {}).get("enacted_actions") or []
    assert enacted and enacted[0].get("action") == "allow_monitor"


@pytest.mark.asyncio
async def test_resume_without_pending_errors(investigation_service):
    events = await collect_async(
        investigation_service.resume_investigation_action("inv_nope", approved=True),
    )
    assert events[0]["event"] == "error"


@pytest.mark.asyncio
async def test_persists_to_kv_when_connected(
    fake_aerospike, fake_graph, action_services_bound, mock_pipeline_patches,
):
    svc = InvestigationService(fake_aerospike, fake_graph, engine_name="mock")
    await svc.initialize()
    fake_aerospike.is_connected.return_value = True

    mock_alert, mock_data = mock_pipeline_patches
    mock_alert.return_value = {"alert_evidence": LOW_RISK_ALERT, "trace_events": []}
    mock_data.return_value = {"initial_evidence": LOW_RISK_INITIAL, "trace_events": []}

    with patch("workflow.engines.mock_engine.execute_action") as mock_action:
        mock_action.return_value = {"status": "executed", "action": "allow_monitor"}
        await collect_async(svc.stream_investigation("U0001234", "inv_kv_persist"))

    fake_aerospike.put_investigation.assert_called_once()
    args = fake_aerospike.put_investigation.call_args[0]
    assert args[0] == "inv_kv_persist"
    assert args[1]["status"] == "completed"


@pytest.mark.asyncio
async def test_get_workflow_steps_four_phases(investigation_service):
    steps = investigation_service.get_workflow_steps()
    assert [s["id"] for s in steps] == [
        "alert_validation",
        "data_collection",
        "llm_agent",
        "report_generation",
    ]
