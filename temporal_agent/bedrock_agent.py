from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

from .models import Principal

if TYPE_CHECKING:
    from .tools import SharePointTools


SYSTEM_PROMPT = """You are a SharePoint temporal-query agent.
Answer by selecting only from the provided allow-listed tools. Never invent
facts, citations, access decisions, dates, or graph queries.
Use query_temporal_graph for historical, as-of, and change questions; use
search_sharepoint_current for current content; use exact-version tools for source
detail. Dates sent to temporal tools must be ISO-8601 UTC timestamps.
The only temporal relationship values are: owner, retention_period, risk, and
exception_status. Map words such as owned, ownership, or accountable to owner.
Historical evidence is already filtered by current access and must never be
reconstructed from inaccessible sources.

In the final answer, state the answer directly and include entity, relationship,
valid interval, source artifact/version, and citation for each material claim.
Copy valid-from and valid-to exactly from tool evidence. Never substitute
recorded or modified timestamps for validity. Clearly identify every conflicting
claim and its evidence. Say when no currently accessible evidence supports the
answer. Return only the final answer; never output thinking or reasoning tags.
"""


class BedrockTemporalAgent:
    """Model-driven planner restricted to SharePointTools' allow-list."""

    def __init__(self, tools: SharePointTools, model_id: str | None = None,
                 client: Any | None = None):
        self.tools = tools
        self.model_id = model_id or os.environ.get(
            "BEDROCK_AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
        if client is None:
            import boto3
            client = boto3.client(
                "bedrock-runtime",
                region_name=os.environ.get("AWS_REGION", "us-east-1"))
        self.client = client

    def ask(self, question: str, principal: Principal) -> dict:
        messages = [{"role": "user", "content": [{"text": question}]}]
        evidence: list[dict] = []
        evidence_keys: set[str] = set()
        trace: list[dict] = []
        for _ in range(6):
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig={"tools": self._tool_specs(), "toolChoice": {"auto": {}}},
                inferenceConfig={"maxTokens": 1200, "temperature": 0.0},
            )
            assistant = response["output"]["message"]
            messages.append(assistant)
            uses = [
                block["toolUse"] for block in assistant.get("content", [])
                if "toolUse" in block
            ]
            if not uses:
                answer = "".join(
                    block.get("text", "") for block in assistant.get("content", [])
                ).strip()
                answer = re.sub(
                    r"<thinking>.*?</thinking>", "", answer,
                    flags=re.IGNORECASE | re.DOTALL).strip()
                return {
                    "answer": answer or "No answer was produced.",
                    "evidence": evidence,
                    "tool_trace": trace,
                    "model_id": self.model_id,
                }
            results = []
            for use in uses:
                name = use["name"]
                arguments = self._normalize(name, use.get("input", {}))
                try:
                    value = self.tools.call(name, arguments, principal)
                    status = "success"
                    for item in value.get("evidence", []):
                        key = json.dumps(item, sort_keys=True, default=str)
                        if key not in evidence_keys:
                            evidence_keys.add(key)
                            evidence.append(item)
                except Exception as exc:
                    value, status = {"error": str(exc)}, "error"
                trace.append({"tool": name, "arguments": arguments, "status": status})
                results.append({"toolResult": {
                    "toolUseId": use["toolUseId"],
                    "content": [{"json": value}],
                    "status": status,
                }})
            messages.append({"role": "user", "content": results})
        raise RuntimeError("Bedrock agent exceeded the maximum tool-use turns")

    @staticmethod
    def _normalize(name: str, arguments: dict) -> dict:
        arguments = dict(arguments)
        if name == "query_temporal_graph":
            relationship = arguments.get("relationship")
            aliases = {
                "owned": "owner",
                "ownership": "owner",
                "accountable": "owner",
                "retention": "retention_period",
                "exception": "exception_status",
            }
            if relationship in aliases:
                arguments["relationship"] = aliases[relationship]
            for field in ("as_of", "changed_from", "changed_to"):
                value = arguments.get(field)
                if isinstance(value, str) and re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", value
                ):
                    suffix = (
                        "T00:00:00Z"
                        if field == "changed_from"
                        else "T23:59:59Z"
                    )
                    arguments[field] = value + suffix
        return arguments

    def _tool_specs(self) -> list[dict]:
        return [
            {"toolSpec": {
                "name": schema["name"],
                "description": schema["description"],
                "inputSchema": {"json": schema["inputSchema"]},
            }}
            for schema in self.tools.schemas()
        ]
