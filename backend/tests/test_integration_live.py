"""Optional live-stack tests (skipped when docker is not running)."""

import json

import httpx
import pytest


def _stack_available() -> bool:
    try:
        r = httpx.get("http://localhost:4000/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.integration
def test_health_endpoint():
    if not _stack_available():
        pytest.skip("live stack not running on localhost:4000")
    r = httpx.get("http://localhost:4000/health", timeout=5.0)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "healthy"


@pytest.mark.integration
def test_investigation_steps_endpoint():
    if not _stack_available():
        pytest.skip("live stack not running on localhost:4000")
    r = httpx.get("http://localhost:4000/investigation/steps", timeout=5.0)
    assert r.status_code == 200
    data = r.json()
    assert data.get("total") == 4
    assert len(data.get("steps") or []) == 4


@pytest.mark.integration
def test_mock_engine_stream_when_configured():
    """Full SSE smoke test — only passes when backend runs with INVESTIGATION_ENGINE=mock."""
    if not _stack_available():
        pytest.skip("live stack not running on localhost:4000")

    user_id = "U0003905"
    events = []
    event_name = None
    data_lines = []

    with httpx.Client(timeout=60.0) as client:
        with client.stream(
            "GET",
            f"http://localhost:4000/investigation/{user_id}/stream",
            headers={"Accept": "text/event-stream"},
        ) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())
                elif line == "" and event_name:
                    payload = json.loads("\n".join(data_lines)) if data_lines else {}
                    events.append((event_name, payload))
                    if event_name in ("complete", "error"):
                        break
                    event_name = None
                    data_lines = []

    types = {e[0] for e in events}
    assert "start" in types, types
    engine = next((p.get("engine") for n, p in events if n == "start"), None)

    if engine != "mock":
        pytest.skip(f"backend engine is {engine!r}, not mock — set INVESTIGATION_ENGINE=mock")

    assert "complete" in types, f"mock stream did not complete: {types}"
    assert "error" not in types
