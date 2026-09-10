from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .mcp import MCPService
from .orchestrator import AgentOrchestrator
from .source import (
    GatewayContentSource,
    MockContentSource,
    mock_source_from_payload,
)
from .tools import SharePointTools


@dataclass
class RuntimeApp:
    orchestrator: AgentOrchestrator | None = None
    mcp: MCPService | None = None


def load_mock_source(fixtures: Path | None = None) -> MockContentSource:
    root = fixtures or Path(__file__).parent.parent / "fixtures"
    raw = json.loads((root / "repository.json").read_text())
    events = [json.loads(line) for line in (root / "changes.jsonl").read_text().splitlines() if line]
    return mock_source_from_payload(raw, events)


def build_orchestrator_runtime() -> RuntimeApp:
    from .remote_mcp import RemoteMCPTools

    tools = RemoteMCPTools()
    return RuntimeApp(orchestrator=AgentOrchestrator(tools))


def build_tools_runtime() -> RuntimeApp:
    table_name = os.environ.get("TEMPORAL_FACTS_TABLE")
    if not table_name:
        raise RuntimeError(
            "TEMPORAL_FACTS_TABLE is required for the MCP tools runtime"
        )
    from .aws_backend import DynamoTemporalGraphStore
    from .gateway_client import GatewayMCPClient

    gateway_url = os.environ.get("SOURCE_GATEWAY_URL")
    if not gateway_url:
        raise RuntimeError(
            "SOURCE_GATEWAY_URL is required for the MCP tools runtime"
        )
    source = GatewayContentSource(GatewayMCPClient(
        gateway_url=gateway_url,
        user_pool_id=os.environ["SOURCE_GATEWAY_USER_POOL_ID"],
        client_id=os.environ["SOURCE_GATEWAY_CLIENT_ID"],
        token_url=os.environ["SOURCE_GATEWAY_TOKEN_URL"],
        region=os.environ.get("AWS_REGION", "us-east-1"),
    ))
    store = DynamoTemporalGraphStore(
        table_name, os.environ.get("AWS_REGION", "us-east-1"))
    store.load_all()
    tools = SharePointTools(source, store)
    return RuntimeApp(mcp=MCPService(tools))
