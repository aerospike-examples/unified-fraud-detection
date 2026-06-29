"""Async tests for the mock investigation engine (no LLM)."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.fixtures import LOW_RISK_ALERT, LOW_RISK_INITIAL, SAMPLE_ALERT, SAMPLE_INITIAL
from tests.helpers import collect_async as _collect_events


class TestMockEngineHelpers(unittest.TestCase):
    def test_specialist_findings_from_metrics(self):
        from workflow.engines.mock_engine import _mock_specialist_findings

        findings = _mock_specialist_findings(SAMPLE_INITIAL)
        self.assertIn("network_analyst", findings)
        self.assertIn("fraud-ring", findings["network_analyst"].lower())
        self.assertIn("velocity", findings["velocity_analyst"].lower())

    def test_primary_account_prefers_flagged(self):
        from workflow.engines.mock_engine import _primary_account_id

        self.assertEqual(_primary_account_id(SAMPLE_INITIAL), "A000123401")


class TestMockEngineFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake_as = MagicMock()
        self.fake_as.is_connected.return_value = False
        self.fake_graph = MagicMock()

    @patch("workflow.engines.mock_engine.recall_cases", new_callable=AsyncMock, return_value=[])
    @patch("workflow.engines.mock_engine.store_case", new_callable=AsyncMock)
    @patch("workflow.engines.mock_engine.execute_action")
    @patch("workflow.engines.mock_engine.data_collection_node")
    @patch("workflow.engines.mock_engine.alert_validation_node")
    async def test_high_risk_pauses_for_hitl(
        self, mock_alert, mock_data, mock_execute, mock_store, mock_recall,
    ):
        from workflow.engines.mock_engine import MockEngine

        mock_alert.return_value = {
            "alert_evidence": SAMPLE_ALERT,
            "trace_events": [],
        }
        mock_data.return_value = {
            "initial_evidence": SAMPLE_INITIAL,
            "trace_events": [],
        }

        eng = MockEngine(self.fake_as, self.fake_graph)
        await eng.initialize()

        events = await _collect_events(
            eng.run_investigation("U0001234", "inv_test_hitl"),
        )

        types = [e.get("type") for e in events]
        self.assertIn("action_confirmation_required", types)
        self.assertIn("_paused", types)
        mock_execute.assert_not_called()

        trace_types = [
            e["event"]["type"]
            for e in events
            if e.get("type") == "trace" and e.get("event")
        ]
        self.assertIn("specialist_finding", trace_types)
        self.assertIn("assessment", trace_types)

    @patch("workflow.engines.mock_engine.recall_cases", new_callable=AsyncMock, return_value=[])
    @patch("workflow.engines.mock_engine.store_case", new_callable=AsyncMock)
    @patch("workflow.engines.mock_engine.execute_action")
    @patch("workflow.engines.mock_engine.data_collection_node")
    @patch("workflow.engines.mock_engine.alert_validation_node")
    async def test_low_risk_completes_without_pause(
        self, mock_alert, mock_data, mock_execute, mock_store, mock_recall,
    ):
        from workflow.engines.mock_engine import MockEngine

        low_initial = LOW_RISK_INITIAL
        mock_alert.return_value = {
            "alert_evidence": LOW_RISK_ALERT,
            "trace_events": [],
        }
        mock_data.return_value = {"initial_evidence": low_initial, "trace_events": []}
        mock_execute.return_value = {"status": "executed", "action": "allow_monitor"}

        eng = MockEngine(self.fake_as, self.fake_graph)
        await eng.initialize()

        events = await _collect_events(
            eng.run_investigation("U0001234", "inv_test_complete"),
        )

        types = [e.get("type") for e in events]
        self.assertIn("complete", types)
        self.assertNotIn("_paused", types)
        mock_execute.assert_called_once()

    @patch("workflow.engines.mock_engine.recall_cases", new_callable=AsyncMock, return_value=[])
    @patch("workflow.engines.mock_engine.store_case", new_callable=AsyncMock)
    @patch("workflow.engines.mock_engine.execute_action")
    @patch("workflow.engines.mock_engine.data_collection_node")
    @patch("workflow.engines.mock_engine.alert_validation_node")
    async def test_resume_after_approval(
        self, mock_alert, mock_data, mock_execute, mock_store, mock_recall,
    ):
        from workflow.engines.mock_engine import MockEngine

        mock_alert.return_value = {"alert_evidence": SAMPLE_ALERT, "trace_events": []}
        mock_data.return_value = {"initial_evidence": SAMPLE_INITIAL, "trace_events": []}
        mock_execute.return_value = {"status": "executed", "action": "temporary_freeze"}

        eng = MockEngine(self.fake_as, self.fake_graph)
        await eng.initialize()

        run_events = await _collect_events(
            eng.run_investigation("U0001234", "inv_test_resume"),
        )
        paused = next(e for e in run_events if e.get("type") == "_paused")
        fc_id = paused["data"]["fc_id"]

        resume_events = await _collect_events(
            eng.resume_investigation(
                "U0001234",
                "inv_test_resume",
                fc_id=fc_id,
                approved=True,
                payload=paused["data"],
            ),
        )

        types = [e.get("type") for e in resume_events]
        self.assertIn("complete", types)
        mock_execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
