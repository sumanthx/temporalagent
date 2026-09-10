from __future__ import annotations

from typing import Any

from .bedrock_agent import BedrockTemporalAgent
from .models import Principal


class AgentOrchestrator:
    """AgentCore Runtime boundary for Bedrock-driven tool orchestration."""

    def __init__(self, tools: Any, agent: Any | None = None):
        self.tools = tools
        self.agent = agent

    def invoke(self, payload: dict, principal: Principal) -> dict:
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        agent = self.agent or BedrockTemporalAgent(self.tools)
        return agent.ask(prompt, principal)
