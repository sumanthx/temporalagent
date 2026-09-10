from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .clock import LogicalClock
from .ingestion import ChangeIngester
from .mcp import MCPService
from .models import SourceVersion
from .orchestrator import AgentOrchestrator
from .source import MockContentSource
from .store import TemporalGraphStore
from .tools import SharePointTools


@dataclass
class DemoApp:
    source: MockContentSource
    store: TemporalGraphStore
    ingester: ChangeIngester
    tools: SharePointTools
    orchestrator: AgentOrchestrator
    mcp: MCPService


def build_demo(fixtures: Path | None = None) -> DemoApp:
    if os.environ.get("RUNTIME_KIND") == "orchestrator":
        from .remote_mcp import RemoteMCPTools
        tools = RemoteMCPTools()
        return DemoApp(
            source=None, store=None, ingester=None, tools=tools,
            orchestrator=AgentOrchestrator(tools), mcp=None)
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
    source = MockContentSource(versions, events)
    table_name = os.environ.get("TEMPORAL_FACTS_TABLE")
    if table_name:
        from .aws_backend import DynamoTemporalGraphStore
        store = DynamoTemporalGraphStore(
            table_name, os.environ.get("AWS_REGION", "us-east-1"))
        store.load_all()
    else:
        store = TemporalGraphStore()
    clock = LogicalClock()
    ingester = ChangeIngester(source, store, clock)
    tools = SharePointTools(source, store)
    app = DemoApp(source, store, ingester, tools, AgentOrchestrator(tools), MCPService(tools))
    if not table_name:
        ingester.replay()
    return app
