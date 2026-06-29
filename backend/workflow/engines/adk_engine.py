"""
ADK investigation engine — thin wrapper around the existing runner.
"""

import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from workflow.engines.base import BaseInvestigationEngine
from workflow.llm import LLMConfig, resolve_adk_model
from workflow.runner import (
    build_runner,
    get_workflow_steps,
    run_investigation,
    resume_investigation,
)

logger = logging.getLogger("investigation.engines.adk")


class AdkEngine(BaseInvestigationEngine):
    """Google ADK SequentialAgent backend (reference implementation)."""

    def __init__(
        self,
        aerospike_service: Any,
        graph_service: Any,
        llm_config: Optional[LLMConfig] = None,
    ):
        self.aerospike_service = aerospike_service
        self.graph_service = graph_service
        self.llm_config = llm_config or LLMConfig.from_env()
        self.model = resolve_adk_model(self.llm_config)
        self._runner = None

    @property
    def engine_name(self) -> str:
        return "adk"

    async def initialize(self) -> None:
        self._runner = build_runner(
            self.aerospike_service,
            self.graph_service,
            self.model,
        )
        logger.info("AdkEngine initialized (model=%s)", self.model)

    async def close(self) -> None:
        if self._runner:
            self._runner.close()
            self._runner = None

    def get_workflow_steps(self) -> List[Dict[str, str]]:
        return get_workflow_steps()

    async def run_investigation(
        self,
        user_id: str,
        investigation_id: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not self._runner:
            await self.initialize()
        async for event in run_investigation(self._runner, user_id, investigation_id):
            yield event

    async def resume_investigation(
        self,
        user_id: str,
        investigation_id: str,
        fc_id: str,
        approved: bool,
        hint: str = "",
        payload: Optional[Dict[str, Any]] = None,
        override: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not self._runner:
            await self.initialize()
        async for event in resume_investigation(
            self._runner,
            user_id,
            investigation_id,
            fc_id=fc_id,
            approved=approved,
            hint=hint,
            payload=payload,
            override=override,
        ):
            yield event
