from __future__ import annotations

import re
import os

from .models import Principal
from .tools import SharePointTools


class AgentOrchestrator:
    """AgentCore Runtime boundary; deterministic local planner substitutes for Bedrock."""

    def __init__(self, tools: SharePointTools):
        self.tools = tools

    def invoke(self, payload: dict, principal: Principal) -> dict:
        if "tool" in payload:
            result = self.tools.call(payload["tool"], payload.get("arguments", {}), principal)
            return {"answer": self._render(result), "tool_result": result}
        prompt = payload.get("prompt", "")
        if os.environ.get("BEDROCK_AGENT_MODEL_ID"):
            from .bedrock_agent import BedrockTemporalAgent
            return BedrockTemporalAgent(self.tools).ask(prompt, principal)
        entity = self._entity(prompt)
        relationship = self._relationship(prompt)
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", prompt)
        if "between" in prompt.lower() and len(dates) >= 2:
            result = self.tools.query_temporal_graph(
                entity=entity, relationship=relationship,
                changed_from=dates[0] + "T00:00:00Z",
                changed_to=dates[1] + "T23:59:59Z", principal=principal)
        else:
            as_of = (dates[0] + "T23:59:59Z") if dates else None
            result = self.tools.query_temporal_graph(
                entity=entity, relationship=relationship, as_of=as_of,
                principal=principal)
        return {"answer": self._render(result), "evidence": result["evidence"]}

    @staticmethod
    def _entity(prompt):
        match = re.search(r"(?:owner of|owns|about)\s+([A-Z][\w -]+?)(?:\s+(?:as of|between)|[?.]|$)", prompt)
        return match.group(1).strip() if match else None

    @staticmethod
    def _relationship(prompt):
        lowered = prompt.lower()
        mappings = {
            "owner": "owner",
            "retention": "retention_period",
            "risk": "risk",
            "exception": "exception_status",
        }
        return next((value for token, value in mappings.items() if token in lowered), None)

    @staticmethod
    def _render(result):
        evidence = result.get("evidence")
        if evidence is None:
            return "Tool completed with structured result."
        if not evidence:
            return "No accessible evidence matched."
        return "\n".join(
            f"{e['entity']} — {e['relationship']} = {e['value']} "
            f"[{e['validity']['from']} to {e['validity']['to']}); "
            f"{e['source']['artifact']} v{e['source']['version_id']}; {e['citation']}"
            for e in evidence
        )
