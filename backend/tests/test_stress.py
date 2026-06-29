"""Concurrency and load tests using the mock engine (offline)."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from tests.fixtures import LOW_RISK_ALERT, LOW_RISK_INITIAL
from tests.helpers import collect_async, validate_service_sse

CONCURRENCY = int(os.environ.get("STRESS_CONCURRENCY", "25"))


@pytest.fixture
def low_risk_patched(mock_pipeline_patches):
    mock_alert, mock_data = mock_pipeline_patches
    mock_alert.return_value = {"alert_evidence": LOW_RISK_ALERT, "trace_events": []}
    mock_data.return_value = {"initial_evidence": LOW_RISK_INITIAL, "trace_events": []}
    return mock_alert, mock_data


@pytest.mark.stress
@pytest.mark.asyncio
async def test_concurrent_investigations_complete(investigation_service, low_risk_patched):
    """Run many investigations in parallel; all should finish with zero LLM calls."""
    concurrency = CONCURRENCY

    async def one_run(idx: int):
        inv_id = f"inv_stress_{idx:04d}"
        events = await collect_async(
            investigation_service.stream_investigation("U0001234", inv_id),
        )
        return idx, validate_service_sse(events, expect_complete=True, engine="mock")

    started = time.perf_counter()
    results = await asyncio.gather(*[one_run(i) for i in range(concurrency)])
    elapsed = time.perf_counter() - started

    assert len(results) == concurrency
    ids = {r[1]["investigation_id"] for r in results}
    assert len(ids) == concurrency
    assert elapsed < max(30.0, concurrency * 0.5), f"too slow: {elapsed:.1f}s for {concurrency}"

    # No leaked pending confirmations
    assert not investigation_service._pending_confirmations


@pytest.mark.stress
@pytest.mark.asyncio
async def test_concurrent_hitl_pause_and_resume(investigation_service, high_risk_pipeline):
    """Several high-risk runs pause; resume each independently."""
    count = min(CONCURRENCY, 10)

    async def run_and_resume(idx: int):
        inv_id = f"inv_hitl_{idx:04d}"
        first = await collect_async(
            investigation_service.stream_investigation("U0001234", inv_id),
        )
        validate_service_sse(first, expect_hitl=True, engine="mock")
        second = await collect_async(
            investigation_service.resume_investigation_action(inv_id, approved=True),
        )
        validate_service_sse(second, expect_complete=True, engine="mock", require_all_nodes=False)

    await asyncio.gather(*[run_and_resume(i) for i in range(count)])


@pytest.mark.stress
@pytest.mark.asyncio
async def test_rapid_sequential_investigations(investigation_service, low_risk_patched):
    """Back-to-back investigations on the same user should not cross-contaminate state."""
    for i in range(20):
        inv_id = f"inv_seq_{i:04d}"
        events = await collect_async(
            investigation_service.stream_investigation("U0001234", inv_id),
        )
        summary = validate_service_sse(events, expect_complete=True, engine="mock")
        assert summary["investigation_id"] == inv_id
        result = investigation_service.get_investigation_result(inv_id)
        assert result and result.get("user_id") == "U0001234"
