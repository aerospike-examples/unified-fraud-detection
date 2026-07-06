"""Shared SSE translation helpers (engine-neutral)."""

from typing import Any, Dict


def specialist_findings(state: Dict[str, Any]) -> Dict[str, str]:
    return {
        "network_analyst": state.get("network_findings") or "",
        "device_analyst": state.get("device_findings") or "",
        "velocity_analyst": state.get("velocity_findings") or "",
    }


def merged_tool_calls(state: Dict[str, Any]) -> list:
    specialist = []
    for key in (
        "specialist_tool_calls_network_analyst",
        "specialist_tool_calls_device_analyst",
        "specialist_tool_calls_velocity_analyst",
    ):
        specialist.extend(state.get(key) or [])
    return specialist + (state.get("tool_calls") or [])
