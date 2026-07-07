"""
Investigation Service

Manages fraud investigations via a pluggable engine (ADK or LangGraph).
Provides SSE streaming for real-time progress and human-in-the-loop approval.
"""

import os
import uuid
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, AsyncGenerator, Callable, List, Optional

from workflow.engines import get_engine, BaseInvestigationEngine
from workflow.llm import LLMConfig

logger = logging.getLogger("investigation.service")


class InvestigationService:
    """Service for managing fraud investigations."""

    def __init__(
        self,
        aerospike_service: Any,
        graph_service: Any,
        engine_name: Optional[str] = None,
        llm_config: Optional[LLMConfig] = None,
    ):
        self.aerospike_service = aerospike_service
        self.graph_service = graph_service
        self.engine_name = (
            engine_name or os.environ.get("INVESTIGATION_ENGINE") or "adk"
        ).strip().lower()
        self.llm_config = llm_config or LLMConfig.from_env()

        self.engine: Optional[BaseInvestigationEngine] = None

        self._active_investigations: Dict[str, Dict[str, Any]] = {}
        self._investigation_results: Dict[str, Dict[str, Any]] = {}
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}
        self._checkpoint_persisted: set[str] = set()

        # Decoupled run infrastructure: the investigation runs as a background
        # task that persists progress to KV independent of any SSE client. SSE
        # endpoints are subscribers that replay buffered events and can re-attach
        # after a disconnect, so navigating away / losing the connection never
        # kills the run — the report still gets written and a decision can be
        # made later from the persisted state.
        self._run_tasks: Dict[str, asyncio.Task] = {}
        self._event_buffers: Dict[str, List[Dict[str, Any]]] = {}
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._run_done: Dict[str, bool] = {}

        logger.info(
            "Investigation service initialized (engine=%s, llm_provider=%s)",
            self.engine_name,
            self.llm_config.provider,
        )

    async def initialize(self):
        """Build the selected investigation engine."""
        try:
            self.engine = get_engine(
                self.engine_name,
                self.aerospike_service,
                self.graph_service,
                self.llm_config,
            )
            await self.engine.initialize()
            logger.info("%s investigation engine initialized", self.engine.engine_name)
        except Exception as e:
            logger.error("Failed to initialize investigation service: %s", e)
            raise

    async def close(self):
        """Clean up resources."""
        if self.engine:
            await self.engine.close()
        logger.info("Investigation service closed")

    def get_workflow_steps(self) -> list[Dict[str, str]]:
        """Get list of workflow steps for UI."""
        if self.engine:
            return self.engine.get_workflow_steps()
        return get_engine(
            self.engine_name,
            self.aerospike_service,
            self.graph_service,
            self.llm_config,
        ).get_workflow_steps()

    def _running_investigation_for_user(self, user_id: str) -> Optional[str]:
        """Return an in-flight investigation id for this user, if any."""
        stale_seconds = 10 * 60
        now = datetime.now()
        for inv_id, meta in list(self._active_investigations.items()):
            if meta.get("user_id") != user_id or meta.get("status") != "running":
                continue
            started = meta.get("started_at")
            if started:
                try:
                    age = (now - datetime.fromisoformat(started)).total_seconds()
                    if age > stale_seconds:
                        logger.warning(
                            "Clearing stale investigation %s for %s (%.0fs old)",
                            inv_id,
                            user_id,
                            age,
                        )
                        meta["status"] = "error"
                        meta["error"] = "Timed out — client disconnected"
                        continue
                except Exception:
                    pass
            return inv_id
        return None

    async def start_investigation(
        self,
        user_id: str,
        triggered_by: str = "manual",
    ) -> str:
        """Start a new investigation for a user."""
        investigation_id = f"inv_{uuid.uuid4().hex[:12]}"

        self._active_investigations[investigation_id] = {
            "user_id": user_id,
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "triggered_by": triggered_by,
            "current_step": "alert_validation",
        }

        logger.info("Started investigation %s for user %s", investigation_id, user_id)
        return investigation_id

    def _kv_payload_from_state(
        self,
        investigation_id: str,
        user_id: str,
        final_state: Dict[str, Any],
        status: str,
    ) -> Dict[str, Any]:
        completed_at = datetime.now().isoformat()
        completed_steps = (
            ["alert_validation", "data_collection", "llm_agent", "report_generation"]
            if status == "completed"
            else ["alert_validation", "data_collection", "llm_agent"]
        )
        payload = {
            "investigation_id": investigation_id,
            "user_id": user_id,
            "completed_at": completed_at,
            "status": status,
            "initial_evidence": final_state.get("initial_evidence", {}),
            "final_assessment": final_state.get("final_assessment", {}),
            "tool_calls": final_state.get("tool_calls", []),
            "spec_findings": final_state.get("specialist_findings", {}),
            "prior_cases": final_state.get("prior_cases", []),
            "enacted_actions": final_state.get("enacted_actions", []),
            "agent_iterations": final_state.get("agent_iterations", 0),
            "report_markdown": final_state.get("report_markdown", ""),
            "completed_steps": completed_steps,
        }
        pending = final_state.get("pending_action")
        if pending:
            payload["pending_action"] = pending
        return payload

    def _persist_to_kv(
        self,
        investigation_id: str,
        user_id: str,
        final_state: Dict[str, Any],
        status: str = "completed",
    ) -> None:
        """Write investigation snapshot to Aerospike (UI restore; SI on investigations.user_id)."""
        if not final_state or not self.aerospike_service or not self.aerospike_service.is_connected():
            return
        try:
            kv_data = self._kv_payload_from_state(
                investigation_id, user_id, final_state, status
            )
            self.aerospike_service.put_investigation(investigation_id, kv_data)
            self._investigation_results[investigation_id] = {
                "user_id": user_id,
                "completed_at": kv_data["completed_at"],
                "state": {**final_state, **kv_data},
            }
            logger.info(
                "Investigation %s persisted to KV store (status=%s)",
                investigation_id,
                status,
            )
        except Exception as e:
            logger.warning("Failed to persist investigation to KV: %s", e)

    async def _consume(
        self,
        investigation_id: str,
        user_id: str,
        event_agen: AsyncGenerator[Dict[str, Any], None],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Translate runner events into SSE events, handle HITL pauses, persist result."""
        final_state = None
        paused = False

        async for event in event_agen:
            event_type = event.get("type", "unknown")

            if event_type == "trace":
                trace = event.get("event", {})
                yield {"event": "trace", "data": trace}
                if investigation_id in self._active_investigations:
                    self._active_investigations[investigation_id]["current_step"] = trace.get(
                        "node", ""
                    )

            elif event_type == "action_confirmation_required":
                yield {"event": "action_confirmation_required", "data": event.get("data", {})}

            elif event_type == "_paused":
                data = event.get("data", {})
                self._pending_confirmations[investigation_id] = {**data, "user_id": user_id}
                if investigation_id in self._active_investigations:
                    self._active_investigations[investigation_id]["status"] = "awaiting_confirmation"
                paused = True

            elif event_type == "state_update":
                data = event.get("data", {})
                yield {
                    "event": "progress",
                    "data": {
                        "node": event.get("node", ""),
                        "phase": data.get("current_phase", ""),
                        **{k: v for k, v in data.items() if k != "trace_events"},
                    },
                }
                if not final_state:
                    final_state = {}
                final_state.update(data)
                if data.get("report_markdown") and investigation_id not in self._checkpoint_persisted:
                    self._checkpoint_persisted.add(investigation_id)
                    persist_status = "awaiting_confirmation" if paused else "in_progress"
                    self._persist_to_kv(
                        investigation_id, user_id, final_state, status=persist_status
                    )

            elif event_type == "metrics":
                yield {
                    "event": "metrics",
                    "data": {
                        "investigation_id": investigation_id,
                        "data": event.get("data", {}),
                    },
                }

            elif event_type == "complete":
                yield {
                    "event": "complete",
                    "data": {"investigation_id": investigation_id, "user_id": user_id},
                }

            elif event_type == "error":
                yield {
                    "event": "error",
                    "data": {
                        "error": event.get("error", "Unknown error"),
                        "investigation_id": investigation_id,
                    },
                }

        if paused:
            if final_state:
                pending = self._pending_confirmations.get(investigation_id)
                if pending:
                    final_state = {**final_state, "pending_action": pending}
                self._persist_to_kv(
                    investigation_id, user_id, final_state, status="awaiting_confirmation"
                )
            return

        if final_state:
            completed_at = datetime.now().isoformat()
            self._investigation_results[investigation_id] = {
                "user_id": user_id,
                "completed_at": completed_at,
                "state": final_state,
            }
            self._persist_to_kv(investigation_id, user_id, final_state, status="completed")
            self._checkpoint_persisted.discard(investigation_id)

        self._pending_confirmations.pop(investigation_id, None)
        if investigation_id in self._active_investigations:
            self._active_investigations[investigation_id]["status"] = "completed"

    # ─────────────────────────────────────────────────────────────────────
    # Decoupled run + pub/sub: the run task drives the engine to completion and
    # persists to KV regardless of whether an SSE client is attached.
    # ─────────────────────────────────────────────────────────────────────
    _STREAM_SENTINEL = {"__stream_end__": True}
    _MAX_BUFFERED_INVESTIGATIONS = 100
    _MAX_BUFFER_EVENTS = 4000

    def _publish(self, investigation_id: str, sse_event: Dict[str, Any]) -> None:
        """Buffer an SSE event (for replay) and fan it out to live subscribers."""
        buf = self._event_buffers.setdefault(investigation_id, [])
        buf.append(sse_event)
        if len(buf) > self._MAX_BUFFER_EVENTS:
            del buf[: len(buf) - self._MAX_BUFFER_EVENTS]
        for q in list(self._subscribers.get(investigation_id, [])):
            q.put_nowait(sse_event)

    def _trim_buffers(self) -> None:
        """Drop the oldest finished investigation buffers to bound memory."""
        if len(self._event_buffers) <= self._MAX_BUFFERED_INVESTIGATIONS:
            return
        done_ids = [i for i in self._event_buffers if self._run_done.get(i)]
        for inv_id in done_ids[: len(self._event_buffers) - self._MAX_BUFFERED_INVESTIGATIONS]:
            self._event_buffers.pop(inv_id, None)
            self._run_done.pop(inv_id, None)

    async def _run_and_broadcast(
        self,
        investigation_id: str,
        user_id: str,
        agen_factory: Callable[[], AsyncGenerator[Dict[str, Any], None]],
        resumed: bool = False,
    ) -> None:
        """Drive the engine to completion INDEPENDENT of any SSE client, buffering
        + broadcasting events. A client disconnect can never cancel this."""
        self._publish(
            investigation_id,
            {
                "event": "start",
                "data": {
                    "investigation_id": investigation_id,
                    "user_id": user_id,
                    "steps": self.get_workflow_steps(),
                    "engine": self.engine.engine_name if self.engine else self.engine_name,
                    **({"resumed": True} if resumed else {}),
                },
            },
        )
        try:
            async for ev in self._consume(investigation_id, user_id, agen_factory()):
                self._publish(investigation_id, ev)
        except asyncio.CancelledError:
            # Legitimate task cancellation (e.g. process shutdown) — let it propagate.
            raise
        except BaseException as e:  # noqa: BLE001
            # Catches BaseExceptionGroup([GeneratorExit]) too — ADK's ParallelAgent
            # can raise a *BaseException* group that a plain `except Exception`
            # would miss, leaving the run crashed-but-never-finalized and the UI
            # polling /record forever. Surface it and persist an error record.
            logger.error("Background investigation %s crashed: %r", investigation_id, e)
            self._publish(
                investigation_id,
                {
                    "event": "error",
                    "data": {
                        "error": "The investigation failed and was stopped. Please try again.",
                        "investigation_id": investigation_id,
                    },
                },
            )
            meta = self._active_investigations.get(investigation_id)
            if meta:
                meta["status"] = "error"
                meta["error"] = str(e)
            try:
                self._persist_to_kv(
                    investigation_id,
                    user_id,
                    {"error": str(e)[:500], "report_markdown": ""},
                    status="error",
                )
            except Exception:
                logger.warning("Could not persist error record for %s", investigation_id)
        finally:
            self._run_done[investigation_id] = True
            self._run_tasks.pop(investigation_id, None)
            for q in list(self._subscribers.get(investigation_id, [])):
                q.put_nowait(self._STREAM_SENTINEL)

    def _ensure_run_task(
        self,
        investigation_id: str,
        user_id: str,
        agen_factory: Callable[[], AsyncGenerator[Dict[str, Any], None]],
        resumed: bool = False,
    ) -> None:
        """Start the background run task for this investigation if not already
        running (and not already finished)."""
        if investigation_id in self._run_tasks or self._run_done.get(investigation_id):
            return
        self._run_done[investigation_id] = False
        self._event_buffers.setdefault(investigation_id, [])
        self._trim_buffers()
        task = asyncio.create_task(
            self._run_and_broadcast(investigation_id, user_id, agen_factory, resumed=resumed)
        )
        self._run_tasks[investigation_id] = task

    async def _subscribe(
        self, investigation_id: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield buffered-then-live SSE events for an investigation. Disconnecting
        from this generator only unsubscribes — it never stops the run task."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(investigation_id, []).append(q)
        try:
            # Replay everything so far (a reconnecting client catches up in full).
            for ev in list(self._event_buffers.get(investigation_id, [])):
                yield ev
            if self._run_done.get(investigation_id):
                return
            while True:
                ev = await q.get()
                if ev is self._STREAM_SENTINEL:
                    break
                yield ev
        finally:
            subs = self._subscribers.get(investigation_id)
            if subs and q in subs:
                subs.remove(q)
            if subs is not None and not subs:
                self._subscribers.pop(investigation_id, None)

    async def stream_investigation(
        self,
        user_id: str,
        investigation_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream investigation progress as SSE events (may pause for HITL approval).

        The run itself happens in a background task; this method just subscribes
        (with full replay), so a client disconnect/reconnect never kills the run.
        """
        if not self.engine:
            await self.initialize()

        if not investigation_id:
            # Attach to an in-flight run for this user instead of starting a
            # duplicate (this is also what a reconnecting EventSource lands on).
            existing = self._running_investigation_for_user(user_id)
            if existing:
                logger.info("Attaching stream for %s to in-flight %s", user_id, existing)
                investigation_id = existing
            else:
                investigation_id = await self.start_investigation(user_id)
                self._ensure_run_task(
                    investigation_id,
                    user_id,
                    lambda iid=investigation_id: self.engine.run_investigation(user_id, iid),
                )
        else:
            # Explicit id: attach to a live run or replay its buffer. Never
            # re-run an investigation that already finished/paused (its result is
            # persisted — the client should load it via /investigation/record).
            if investigation_id not in self._run_tasks and not self._run_done.get(investigation_id):
                rec = self.get_investigation_record(investigation_id)
                status = (rec or {}).get("status")
                if status in ("completed", "awaiting_confirmation"):
                    self._run_done[investigation_id] = True
                else:
                    self._ensure_run_task(
                        investigation_id,
                        user_id,
                        lambda iid=investigation_id: self.engine.run_investigation(user_id, iid),
                    )

        async for ev in self._subscribe(investigation_id):
            yield ev

    def has_pending_action(self, investigation_id: str) -> bool:
        """Whether the investigation is paused awaiting analyst approval."""
        return investigation_id in self._pending_confirmations

    async def resume_investigation_action(
        self,
        investigation_id: str,
        approved: bool,
        override: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Resume a paused investigation after analyst approves/rejects the action."""
        pending = self._pending_confirmations.get(investigation_id)
        if not pending:
            yield {
                "event": "error",
                "data": {
                    "error": "No pending action to confirm",
                    "investigation_id": investigation_id,
                },
            }
            return

        user_id = pending["user_id"]
        if not self.engine:
            await self.initialize()

        self._pending_confirmations.pop(investigation_id, None)
        if investigation_id in self._active_investigations:
            self._active_investigations[investigation_id]["status"] = "running"

        # Launch the resume as a fresh background run for the same id so it also
        # survives a client disconnect. Clear the pre-pause buffer so a
        # reconnecting client doesn't replay the old action_confirmation event.
        self._event_buffers[investigation_id] = []
        self._run_done[investigation_id] = False

        def _resume_agen(iid=investigation_id, uid=user_id, p=pending):
            return self.engine.resume_investigation(
                uid,
                iid,
                fc_id=p.get("fc_id", "langgraph_interrupt"),
                approved=approved,
                hint=p.get("hint", ""),
                payload={
                    "decision": p.get("decision"),
                    "account_id": p.get("account_id"),
                    "reason": p.get("reason"),
                },
                override=override,
            )

        self._ensure_run_task(investigation_id, user_id, _resume_agen, resumed=True)

        async for ev in self._subscribe(investigation_id):
            yield ev

    def get_investigation_record(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        """Get a persisted investigation snapshot by ID (O(1) KV lookup)."""
        if self.aerospike_service and self.aerospike_service.is_connected():
            record = self.aerospike_service.get_investigation(investigation_id)
            return self._restore_bin_names(record)
        result = self._investigation_results.get(investigation_id)
        if result:
            state = result.get("state") or {}
            return {
                "investigation_id": investigation_id,
                "user_id": result.get("user_id"),
                **state,
            }
        return None

    def get_investigation_status(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an investigation."""
        if investigation_id in self._active_investigations:
            return self._active_investigations[investigation_id]
        return None

    def get_investigation_result(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        """Get result of a completed investigation."""
        if investigation_id in self._investigation_results:
            return self._investigation_results[investigation_id]

        if self.aerospike_service and self.aerospike_service.is_connected():
            kv_result = self.aerospike_service.get_investigation(investigation_id)
            if kv_result:
                self._investigation_results[investigation_id] = {
                    "user_id": kv_result.get("user_id"),
                    "completed_at": kv_result.get("completed_at"),
                    "state": kv_result,
                }
                return self._investigation_results[investigation_id]

        return None

    @staticmethod
    def _restore_bin_names(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Map short Aerospike bin names back to full API field names."""
        if (
            isinstance(record, dict)
            and "spec_findings" in record
            and "specialist_findings" not in record
        ):
            record["specialist_findings"] = record.pop("spec_findings")
        return record

    def get_user_latest_investigation(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the most recent investigation for a user from KV (sync)."""
        if self.aerospike_service and self.aerospike_service.is_connected():
            return self._restore_bin_names(
                self.aerospike_service.get_user_latest_investigation(user_id)
            )

        user_investigations = [
            {"investigation_id": inv_id, **data}
            for inv_id, data in self._investigation_results.items()
            if data.get("user_id") == user_id
        ]

        if not user_investigations:
            return None

        user_investigations.sort(key=lambda x: x.get("completed_at", ""), reverse=True)
        return user_investigations[0]

    async def get_user_latest_investigation_async(
        self, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """KV lookup with ADK session/artifact fallback when KV is empty."""
        latest = self.get_user_latest_investigation(user_id)
        if latest and latest.get("report_markdown"):
            return latest

        inv_id = (latest or {}).get("investigation_id")
        if not inv_id and self.aerospike_service and self.aerospike_service.is_connected():
            # Re-query in case report was missing from latest snapshot
            retry = self.aerospike_service.get_user_latest_investigation(user_id)
            inv_id = (retry or {}).get("investigation_id")
            if retry and retry.get("report_markdown"):
                latest = retry

        if inv_id and self.engine and hasattr(self.engine, "load_investigation_snapshot"):
            snap = await self.engine.load_investigation_snapshot(user_id, inv_id)
            if snap:
                self._persist_to_kv(
                    inv_id,
                    user_id,
                    {
                        **snap,
                        "specialist_findings": snap.get("specialist_findings", {}),
                    },
                    status=snap.get("status", "completed"),
                )
                return self._restore_bin_names(snap)

        return latest

    def get_user_investigation_history(self, user_id: str) -> list[Dict[str, Any]]:
        """Get investigation history for a user."""
        if self.aerospike_service and self.aerospike_service.is_connected():
            return self.aerospike_service.get_user_investigation_history(user_id)

        history = []
        for inv_id, data in self._investigation_results.items():
            if data.get("user_id") == user_id:
                history.append(
                    {
                        "investigation_id": inv_id,
                        "completed_at": data.get("completed_at"),
                        "risk_level": data.get("state", {})
                        .get("risk_assessment", {})
                        .get("risk_level"),
                        "recommendation": data.get("state", {})
                        .get("decision", {})
                        .get("recommended_action"),
                    }
                )

        return sorted(history, key=lambda x: x.get("completed_at", ""), reverse=True)

    async def get_investigation_report(self, investigation_id: str) -> Optional[str]:
        """Get the markdown report for an investigation (KV, memory, or ADK artifact)."""
        result = self.get_investigation_result(investigation_id)
        if result:
            md = (result.get("state") or {}).get("report_markdown")
            if md:
                return md

        if self.aerospike_service and self.aerospike_service.is_connected():
            kv = self.aerospike_service.get_investigation(investigation_id)
            if kv and kv.get("report_markdown"):
                return kv["report_markdown"]

        if self.engine and hasattr(self.engine, "load_investigation_snapshot"):
            user_id = None
            if investigation_id in self._investigation_results:
                user_id = self._investigation_results[investigation_id].get("user_id")
            if not user_id and self.aerospike_service:
                kv = self.aerospike_service.get_investigation(investigation_id)
                user_id = (kv or {}).get("user_id")
            if user_id:
                snap = await self.engine.load_investigation_snapshot(user_id, investigation_id)
                if snap:
                    return snap.get("report_markdown")

        return None


investigation_service: Optional[InvestigationService] = None
