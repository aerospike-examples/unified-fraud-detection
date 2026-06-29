"""Tests for the polymorphic investigation engine interface."""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEngineFactory(unittest.TestCase):
    def test_supported_engines(self):
        from workflow.engines import SUPPORTED_ENGINES

        self.assertEqual(SUPPORTED_ENGINES, ("adk", "langgraph", "mock"))

    def test_unknown_engine_raises(self):
        from workflow.engines import get_engine

        fake = MagicMock()
        fake.is_connected.return_value = False
        with self.assertRaises(ValueError):
            get_engine("unknown", fake, fake)

    def test_langgraph_engine_steps(self):
        try:
            from workflow.engines.langgraph_engine import LangGraphEngine
        except ModuleNotFoundError:
            self.skipTest("langgraph not installed in test environment")

        fake = MagicMock()
        fake.is_connected.return_value = False
        eng = LangGraphEngine(fake, fake)
        self.assertEqual(eng.engine_name, "langgraph")
        ids = [s["id"] for s in eng.get_workflow_steps()]
        self.assertEqual(
            ids,
            ["alert_validation", "data_collection", "llm_agent", "report_generation"],
        )

    def test_get_engine_langgraph(self):
        try:
            from workflow.engines import get_engine
        except ModuleNotFoundError:
            self.skipTest("langgraph not installed in test environment")

        fake = MagicMock()
        fake.is_connected.return_value = False
        eng = get_engine("langgraph", fake, fake)
        self.assertEqual(eng.engine_name, "langgraph")

    def test_get_engine_mock(self):
        from workflow.engines import get_engine

        fake = MagicMock()
        fake.is_connected.return_value = False
        eng = get_engine("mock", fake, fake)
        self.assertEqual(eng.engine_name, "mock")


class TestActionCore(unittest.TestCase):
    def test_decision_sets(self):
        from workflow.action_core import (
            ALL_DECISIONS,
            DESTRUCTIVE_DECISIONS,
            NONDESTRUCTIVE_DECISIONS,
        )

        self.assertIn("temporary_freeze", DESTRUCTIVE_DECISIONS)
        self.assertIn("allow_monitor", NONDESTRUCTIVE_DECISIONS)
        self.assertEqual(ALL_DECISIONS, DESTRUCTIVE_DECISIONS | NONDESTRUCTIVE_DECISIONS)


class TestLLMConfig(unittest.TestCase):
    def test_defaults(self):
        from workflow.llm import LLMConfig

        cfg = LLMConfig.from_env()
        self.assertIn(cfg.provider, ("gemini", "ollama"))

    def test_ollama_adk_model_string(self):
        from workflow.llm import LLMConfig, resolve_adk_model

        cfg = LLMConfig(provider="ollama", ollama_model="mistral")
        self.assertEqual(resolve_adk_model(cfg), "ollama/mistral")


class TestSSEContractHelpers(unittest.TestCase):
    def test_merged_tool_calls_order(self):
        from workflow.sse_helpers import merged_tool_calls

        state = {
            "specialist_tool_calls_network_analyst": [{"tool": "detect_fraud_ring"}],
            "specialist_tool_calls_device_analyst": [{"tool": "get_device_risk_features"}],
            "specialist_tool_calls_velocity_analyst": [{"tool": "get_account_transactions"}],
            "tool_calls": [{"tool": "submit_assessment"}],
        }
        merged = merged_tool_calls(state)
        self.assertEqual(len(merged), 4)
        self.assertEqual(merged[-1]["tool"], "submit_assessment")


if __name__ == "__main__":
    unittest.main()
