"""
LangGraph Workflow Definition

Agentic 4-node workflow:
1. Alert Validation - Get flag context
2. Data Collection - Gather baseline evidence
3. LLM Agent - Tool-calling agent that gathers more data as needed
4. Report Generation - Generate final report

The LLM Agent handles all the reasoning and tool calling internally.
"""

from typing import Dict, Any, AsyncGenerator, Sequence, Optional
import logging
import traceback
import re
import time

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.aerospike import AerospikeSaver
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langchain_core.runnables import RunnableConfig

from workflow.state import InvestigationState, create_initial_state
from workflow.nodes.alert_validation import alert_validation_node
from workflow.nodes.data_collection import data_collection_node
from workflow.nodes.llm_agent import llm_agent_node
from workflow.nodes.report_generation import report_generation_node
from workflow.metrics import get_collector, remove_collector

logger = logging.getLogger('investigation.workflow')


class InstrumentedAerospikeSaver(AerospikeSaver):
    """
    Wraps AerospikeSaver to track checkpoint DB calls and timing
    in the per-investigation MetricsCollector.
    
    Each checkpoint operation (put, put_writes, get_tuple) is timed
    and recorded as a checkpoint call in the metrics.
    """
    
    def _get_investigation_id(self, config: Optional[RunnableConfig]) -> Optional[str]:
        """Extract investigation_id (thread_id) from LangGraph config."""
        try:
            return (config or {}).get("configurable", {}).get("thread_id")
        except Exception:
            return None

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        inv_id = self._get_investigation_id(config)
        start = time.time()
        try:
            result = super().put(config, checkpoint, metadata, new_versions)
            return result
        except Exception as e:
            logger.warning(f"Checkpoint put failed (non-fatal): {e}")
            return config
        finally:
            duration_ms = (time.time() - start) * 1000
            if inv_id:
                try:
                    collector = get_collector(inv_id)
                    collector.track_checkpoint("put", duration_ms)
                    collector.track_db_call("checkpoint_put", "KV", duration_ms)
                except Exception:
                    pass

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        inv_id = self._get_investigation_id(config)
        start = time.time()
        try:
            super().put_writes(config, writes, task_id, task_path)
        except Exception as e:
            logger.warning(f"Checkpoint put_writes failed (non-fatal): {e}")
        finally:
            duration_ms = (time.time() - start) * 1000
            if inv_id:
                try:
                    collector = get_collector(inv_id)
                    collector.track_checkpoint("put_writes", duration_ms)
                    collector.track_db_call("checkpoint_put_writes", "KV", duration_ms)
                except Exception:
                    pass

    def get_tuple(self, config: RunnableConfig):
        inv_id = self._get_investigation_id(config)
        start = time.time()
        try:
            result = super().get_tuple(config)
            return result
        finally:
            duration_ms = (time.time() - start) * 1000
            if inv_id:
                try:
                    collector = get_collector(inv_id)
                    collector.track_checkpoint("get_tuple", duration_ms)
                    collector.track_db_call("checkpoint_get", "KV", duration_ms)
                except Exception:
                    pass


def create_investigation_workflow(
    aerospike_service: Any,
    graph_service: Any,
    ollama_client: Any = None  # Kept for backwards compatibility but not used
) -> StateGraph:
    """
    Create the LangGraph investigation workflow.
    
    New 4-node agentic workflow:
    - alert_validation: Get flag context from KV
    - data_collection: Gather baseline evidence from KV + Graph
    - llm_agent: ReAct agent that uses tools to gather more data
    - report_generation: Generate final markdown report
    
    Args:
        aerospike_service: Aerospike KV service instance
        graph_service: Aerospike Graph service instance
        ollama_client: (Deprecated) HTTP client for Ollama - agent handles this internally
        
    Returns:
        Compiled StateGraph workflow
    """
    
    # Create the workflow graph
    workflow = StateGraph(InvestigationState)
    
    # ------------------------------------------
    # Node 1: Alert Validation
    # ------------------------------------------
    def _alert_validation(state: InvestigationState) -> Dict[str, Any]:
        return alert_validation_node(state, aerospike_service)
    
    workflow.add_node("alert_validation", _alert_validation)
    
    # ------------------------------------------
    # Node 2: Data Collection (KV-only: profile, accounts, devices, features)
    # ------------------------------------------
    def _data_collection(state: InvestigationState) -> Dict[str, Any]:
        return data_collection_node(state, aerospike_service, graph_service)
    
    workflow.add_node("data_collection", _data_collection)
    
    # ------------------------------------------
    # Node 3: LLM Reasoning Agent (with tools)
    # ------------------------------------------
    def _llm_agent(state: InvestigationState) -> Dict[str, Any]:
        return llm_agent_node(state, aerospike_service, graph_service)
    
    workflow.add_node("llm_agent", _llm_agent)
    
    # ------------------------------------------
    # Node 4: Report Generation
    # ------------------------------------------
    async def _report_generation(state: InvestigationState) -> Dict[str, Any]:
        return await report_generation_node(state, ollama_client)
    
    workflow.add_node("report_generation", _report_generation)
    
    # ------------------------------------------
    # Define Edges (Linear Flow)
    # ------------------------------------------
    
    # Set entry point
    workflow.set_entry_point("alert_validation")
    
    # Linear flow through all nodes
    workflow.add_edge("alert_validation", "data_collection")
    workflow.add_edge("data_collection", "llm_agent")
    workflow.add_edge("llm_agent", "report_generation")
    workflow.add_edge("report_generation", END)
    
    # Compile the workflow with Aerospike checkpointer when KV is connected
    if aerospike_service is not None and aerospike_service.is_connected():
        try:
            saver = InstrumentedAerospikeSaver(
                client=aerospike_service.client,
                namespace=aerospike_service.namespace,
            )
            compiled = workflow.compile(checkpointer=saver)
            logger.info("Investigation workflow compiled with Aerospike checkpointer (4-node agentic architecture)")
        except Exception as e:
            logger.error(f"Failed to create AerospikeSaver: {e}")
            compiled = workflow.compile()
            logger.info("Investigation workflow compiled without checkpointer (AerospikeSaver creation failed)")
    else:
        compiled = workflow.compile()
        logger.info("Investigation workflow compiled without checkpointer (Aerospike KV unavailable)")
    
    return compiled


async def run_investigation(
    workflow: StateGraph,
    user_id: str,
    investigation_id: str
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Run the investigation workflow and yield trace events.
    
    Args:
        workflow: Compiled LangGraph workflow
        user_id: User ID to investigate
        investigation_id: Unique investigation ID
        
    Yields:
        Trace events for SSE streaming
    """
    # Initialize metrics collector for this investigation
    metrics_collector = get_collector(investigation_id)
    metrics_collector.reset()
    
    # Create initial state
    initial_state = create_initial_state(investigation_id, user_id)
    
    logger.info(f"Starting investigation {investigation_id} for user {user_id}")
    
    # Config for LangGraph: thread_id required for checkpointing; checkpoint_ns namespaces investigation checkpoints
    config = {
        "configurable": {"thread_id": investigation_id, "checkpoint_ns": "investigation"},
        "recursion_limit": 50,
    }
    
    # Run the workflow with streaming
    current_node = None
    try:
        async for event in workflow.astream(initial_state, config):
            # Extract node name and state updates
            for node_name, state_update in event.items():
                # Track node transitions for metrics
                if current_node != node_name:
                    if current_node:
                        metrics_collector.end_node(current_node)
                    metrics_collector.start_node(node_name)
                    current_node = node_name
                
                # Yield trace events from the state update
                if "trace_events" in state_update:
                    for trace_event in state_update["trace_events"]:
                        yield {
                            "type": "trace",
                            "event": trace_event
                        }
                
                # Yield tool calls if present (for real-time UI updates)
                if "tool_calls" in state_update and state_update["tool_calls"]:
                    for tool_call in state_update["tool_calls"]:
                        # Track tool call in metrics
                        metrics_collector.track_tool_call(tool_call.get("tool", "unknown"))
                        yield {
                            "type": "tool_call",
                            "node": node_name,
                            "data": {
                                "tool": tool_call.get("tool"),
                                "params": tool_call.get("params"),
                                "timestamp": tool_call.get("timestamp")
                            }
                        }
                
                # Yield state update (without trace events to avoid duplication)
                state_copy = {k: v for k, v in state_update.items() if k != "trace_events"}
                if state_copy:
                    yield {
                        "type": "state_update",
                        "node": node_name,
                        "data": state_copy
                    }
        
        # End timing for the last node
        if current_node:
            metrics_collector.end_node(current_node)
        
        # Get final metrics
        final_metrics = metrics_collector.get_metrics()
        logger.info(f"Investigation {investigation_id} completed. Total time: {final_metrics['total_duration_ms']:.2f}ms, "
                   f"DB calls: {final_metrics['total_db_calls']} (KV: {final_metrics['kv_calls']}, Graph: {final_metrics['graph_calls']}), "
                   f"LLM calls: {final_metrics['llm_calls']}")
        
        # Yield metrics event
        yield {
            "type": "metrics",
            "investigation_id": investigation_id,
            "data": final_metrics
        }
        
        # Yield completion event
        yield {
            "type": "complete",
            "investigation_id": investigation_id,
            "user_id": user_id
        }
        
        # Clean up metrics collector
        remove_collector(investigation_id)
        
    except Exception as e:
        logger.error(f"Investigation workflow error: {e}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        
        # Debug: Log what the checkpointer might be trying to serialize
        logger.error("=" * 60)
        logger.error("DEBUG: Analyzing serialization failure")
        logger.error("=" * 60)
        
        try:
            error_str = str(e)
            
            # Identify which set failed
            if "lg_cp_w" in error_str:
                logger.error("❌ Error is in WRITES storage (lg_cp_w set) - put_writes() failed")
                logger.error("   This means List[Dict] of pending writes couldn't be serialized")
            elif "lg_cp" in error_str:
                logger.error("❌ Error is in CHECKPOINT storage (lg_cp set) - put() failed")
            
            # Parse the key from error message
            key_match = re.search(r"\('(\w+)', '(\w+)', '([^']+)'\)", error_str)
            if key_match:
                namespace = key_match.group(1)
                set_name = key_match.group(2)
                key = key_match.group(3)
                logger.error(f"   Namespace: {namespace}")
                logger.error(f"   Set: {set_name}")
                logger.error(f"   Key: {key}")
                
                # Parse key components
                key_parts = key.split("|")
                if len(key_parts) >= 3:
                    logger.error(f"   Thread ID: {key_parts[0]}")
                    logger.error(f"   Checkpoint NS: {key_parts[1]}")
                    logger.error(f"   Checkpoint ID: {key_parts[2]}")
            
            # Check for serialization specific error
            if "SERIALIZER_NONE" in error_str:
                logger.error("")
                logger.error("💡 ROOT CAUSE: Aerospike client has no serializer configured")
                logger.error("   The AerospikeSaver is trying to store List[Dict] but Aerospike")
                logger.error("   can only store primitives (int, str, bytes) without a serializer.")
                logger.error("")
                logger.error("   FIX: Add serializer to Aerospike client config:")
                logger.error("   config = {")
                logger.error("       'hosts': [...],")
                logger.error("       'serialization': (pickle.dumps, pickle.loads)")
                logger.error("   }")
                
        except Exception as debug_err:
            logger.error(f"Debug logging failed: {debug_err}")
        
        logger.error("=" * 60)
        
        # Still emit partial metrics on error
        try:
            if current_node:
                metrics_collector.end_node(current_node)
            partial_metrics = metrics_collector.get_metrics()
            yield {
                "type": "metrics",
                "investigation_id": investigation_id,
                "data": partial_metrics
            }
        except Exception:
            pass
        
        yield {
            "type": "error",
            "investigation_id": investigation_id,
            "user_id": user_id,
            "error": str(e)
        }
        
        # Clean up metrics collector on error
        remove_collector(investigation_id)


def get_workflow_steps() -> list[Dict[str, str]]:
    """Get list of workflow steps for UI display."""
    return [
        {
            "id": "alert_validation",
            "name": "Alert Validation",
            "description": "Extract alert trigger context from flagged account",
            "phase": "context"
        },
        {
            "id": "data_collection",
            "name": "Data Collection",
            "description": "Gather baseline profile, accounts, devices, transactions",
            "phase": "evidence"
        },
        {
            "id": "llm_agent",
            "name": "AI Investigation Agent",
            "description": "LLM agent uses tools to gather additional evidence and make assessment",
            "phase": "reasoning"
        },
        {
            "id": "report_generation",
            "name": "Report Generation",
            "description": "Generate detailed investigation report",
            "phase": "report"
        }
    ]
