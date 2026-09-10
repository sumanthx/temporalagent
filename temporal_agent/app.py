from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .mcp import MCPService
from .models import SourceVersion
from .orchestrator import AgentOrchestrator
from .source import MockContentSource
from .tools import SharePointTools


@dataclass
class RuntimeApp:
    orchestrator: AgentOrchestrator | None = None
    mcp: MCPService | None = None


def load_mock_source(fixtures: Path | None = None) -> MockContentSource:
    root = fixtures or Path(__file__).parent.parent / "fixtures"
    raw = json.loads((root / "repository.json").read_text())
    versions = [
        SourceVersion(
            document_id=v["document_id"], version_id=v["version_id"], title=v["title"],
            path=v["path"], modified_at=v["modified_at"], valid_from=v["valid_from"],
            content=v["content"], claims=tuple(v.get("claims", [])),
            readers=frozenset(v["readers"]), deleted=v.get("deleted", False),
            provenance=v.get("provenance", {}),
        ) for v in raw["versions"]
    ]
    events = [json.loads(line) for line in (root / "changes.jsonl").read_text().splitlines() if line]
    return MockContentSource(versions, events)


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

    source = load_mock_source()
    store = DynamoTemporalGraphStore(
        table_name, os.environ.get("AWS_REGION", "us-east-1"))
    store.load_all()
    tools = SharePointTools(source, store)
    return RuntimeApp(mcp=MCPService(tools))
