"""Pytest fixtures for investigation tests (no docker required)."""

from __future__ import annotations

import os
import sys
from typing import AsyncIterator, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# backend/ on path when running pytest from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures import LOW_RISK_ALERT, LOW_RISK_INITIAL, SAMPLE_ALERT, SAMPLE_INITIAL
from tests.helpers import collect_async


@pytest.fixture
def fake_aerospike():
    svc = MagicMock()
    svc.is_connected.return_value = False
    svc.namespace = "test"
    svc.client = MagicMock()
    svc.put_investigation = MagicMock()
    svc.get_investigation = MagicMock(return_value=None)
    return svc


@pytest.fixture
def fake_graph():
    return MagicMock()


@pytest.fixture
def fake_flagged_service():
    svc = MagicMock()
    svc.freeze_account.return_value = {"success": True}
    svc.mark_monitoring.return_value = {"success": True}
    svc.resolve_account.return_value = {"success": True}
    svc.resolve_flagged_account.return_value = {"success": True}
    return svc


@pytest.fixture
def action_services_bound(fake_flagged_service):
    from workflow.action_core import init_action_services

    init_action_services(fake_flagged_service)
    return fake_flagged_service


@pytest_asyncio.fixture
async def mock_engine(fake_aerospike, fake_graph):
    from workflow.engines.mock_engine import MockEngine

    eng = MockEngine(fake_aerospike, fake_graph)
    await eng.initialize()
    return eng


@pytest_asyncio.fixture
async def investigation_service(fake_aerospike, fake_graph, action_services_bound):
    from services.investigation_service import InvestigationService

    svc = InvestigationService(
        fake_aerospike,
        fake_graph,
        engine_name="mock",
    )
    await svc.initialize()
    return svc


@pytest.fixture
def mock_pipeline_patches():
    """Patch KV pre-steps so tests never touch Aerospike."""
    with (
        patch("workflow.engines.mock_engine.alert_validation_node") as mock_alert,
        patch("workflow.engines.mock_engine.data_collection_node") as mock_data,
        patch("workflow.engines.mock_engine.recall_cases", new_callable=AsyncMock, return_value=[]),
        patch("workflow.engines.mock_engine.store_case", new_callable=AsyncMock),
    ):
        yield mock_alert, mock_data


@pytest.fixture
def high_risk_pipeline(mock_pipeline_patches):
    mock_alert, mock_data = mock_pipeline_patches
    mock_alert.return_value = {"alert_evidence": SAMPLE_ALERT, "trace_events": []}
    mock_data.return_value = {"initial_evidence": SAMPLE_INITIAL, "trace_events": []}
    return mock_alert, mock_data


@pytest.fixture
def low_risk_pipeline(mock_pipeline_patches):
    mock_alert, mock_data = mock_pipeline_patches
    mock_alert.return_value = {"alert_evidence": LOW_RISK_ALERT, "trace_events": []}
    mock_data.return_value = {"initial_evidence": LOW_RISK_INITIAL, "trace_events": []}
    return mock_alert, mock_data
