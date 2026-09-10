import unittest
import json
import urllib.request
from types import SimpleNamespace
from unittest.mock import patch

import boto3
from botocore.validate import validate_parameters

from aws_lambda.source_gateway import dispatch as dispatch_source_gateway
from aws_lambda.source_sync import sync_once
from temporal_agent.app import (
    build_orchestrator_runtime,
    build_tools_runtime,
    load_mock_source,
)
from temporal_agent.bedrock_agent import BedrockTemporalAgent
from temporal_agent.clock import LogicalClock
from temporal_agent.ingestion import ChangeIngester
from temporal_agent.mcp import MCPService
from temporal_agent.models import MAX_TIME, Principal
from temporal_agent.orchestrator import AgentOrchestrator
from temporal_agent.server import principal_from_headers
from temporal_agent.store import TemporalGraphStore
from temporal_agent.source import GatewayContentSource
from temporal_agent.tools import SharePointTools
from aws_lambda.handlers import _repair_valid_intervals
from temporal_agent.gateway_client import GatewayMCPClient
from temporal_agent.gateway_contract import (
    SOURCE_GATEWAY_TOOL_NAMES,
    SOURCE_GATEWAY_TOOLS,
)
from scripts.deploy_gateway import gateway_configuration, target_configuration
from scripts.deploy_runtimes import artifact_type, build_code_artifact


class VerticalSliceTests(unittest.TestCase):
    def setUp(self):
        source = load_mock_source()
        store = TemporalGraphStore()
        ingester = ChangeIngester(source, store, LogicalClock())
        tools = SharePointTools(source, store)
        ingester.replay()
        self.app = SimpleNamespace(
            source=source,
            store=store,
            ingester=ingester,
            tools=tools,
            mcp=MCPService(tools),
        )
        self.alice = Principal("alice")

    def evidence(self, **kwargs):
        return self.app.tools.query_temporal_graph(principal=self.alice, **kwargs)["evidence"]

    def test_as_of_ownership(self):
        jan = self.evidence(entity="Atlas", relationship="owner",
                            as_of="2024-02-01T00:00:00Z")
        apr = self.evidence(entity="Atlas", relationship="owner",
                            as_of="2024-04-01T00:00:00Z")
        self.assertEqual(["Alice"], [e["value"] for e in jan])
        self.assertEqual(["Bob"], [e["value"] for e in apr])

    def test_changes_between_dates(self):
        rows = self.evidence(entity="Atlas", changed_from="2024-02-15T00:00:00Z",
                             changed_to="2024-04-01T00:00:00Z")
        self.assertEqual(["Bob"], [e["value"] for e in rows])

    def test_effective_date_differs_from_modified_date_and_has_lineage(self):
        rows = self.evidence(entity="Customer Records", as_of="2024-04-02T00:00:00Z")
        self.assertEqual("7 years", rows[0]["value"])
        self.assertEqual("2024-02-15T12:00:00Z", rows[0]["provenance"]["modified_at"])
        self.assertEqual("1.0", rows[0]["source"]["version_id"])
        self.assertTrue(rows[0]["citation"].startswith("sharepoint://"))

    def test_current_access_blocks_historical_evidence(self):
        result = self.app.tools.query_temporal_graph(
            entity="Orion", as_of="2024-02-01T00:00:00Z", principal=self.alice)
        self.assertEqual([], result["evidence"])
        self.assertGreater(result["access_filtered_count"], 0)
        with self.assertRaises(PermissionError):
            self.app.tools.retrieve_version_evidence(
                "secret-plan", "1.0", principal=self.alice)

    def test_out_of_order_ingestion_repairs_valid_intervals(self):
        rows = self.evidence(entity="Atlas", relationship="exception_status",
                             as_of="2024-03-01T00:00:00Z")
        self.assertEqual(["open"], [e["value"] for e in rows])
        closed = self.evidence(entity="Atlas", relationship="exception_status",
                               as_of="2024-06-05T00:00:00Z")
        self.assertEqual(["closed"], [e["value"] for e in closed])
        open_record = next(r for r in self.app.store.records if r.value == "open")
        self.assertEqual("2024-06-01T00:00:00Z", open_record.valid_to)
        self.assertEqual("2024-06-10T10:05:00Z", open_record.recorded_from)

    def test_lambda_repairs_valid_intervals(self):
        class FakeTable:
            def __init__(self):
                self.rows = [
                    {
                        "pk": "ENTITY#Atlas",
                        "sk": "v1",
                        "entity": "Atlas",
                        "relationship": "owner",
                        "document_id": "ownership-register",
                        "valid_from": "2024-01-01T00:00:00Z",
                        "valid_to": MAX_TIME,
                    },
                    {
                        "pk": "ENTITY#Atlas",
                        "sk": "v2",
                        "entity": "Atlas",
                        "relationship": "owner",
                        "document_id": "ownership-register",
                        "valid_from": "2024-03-01T00:00:00Z",
                        "valid_to": MAX_TIME,
                    },
                ]
                self.updates = []

            def query(self, **_kwargs):
                return {"Items": self.rows}

            def update_item(self, **kwargs):
                self.updates.append(kwargs)

        table = FakeTable()
        _repair_valid_intervals(
            table, "Atlas", "owner", "ownership-register")
        self.assertEqual(1, len(table.updates))
        self.assertEqual(
            "2024-03-01T00:00:00Z",
            table.updates[0]["ExpressionAttributeValues"][":valid_to"],
        )

    def test_conflicting_claims_are_preserved(self):
        rows = self.evidence(entity="Atlas", relationship="risk",
                             as_of="2024-03-01T00:00:00Z")
        self.assertEqual({"Medium", "High"}, {e["value"] for e in rows})

    def test_deletion_creates_tombstone_and_removes_current_access(self):
        tombstones = [r for r in self.app.store.records
                      if r.document_id == "obsolete-procedure" and r.tombstone]
        self.assertEqual(1, len(tombstones))
        self.assertEqual("deleted", tombstones[0].value)
        self.assertIsNone(self.app.source.get_current("obsolete-procedure"))
        rows = self.evidence(entity="Legacy Approval", as_of="2024-02-01T00:00:00Z")
        self.assertEqual([], rows)

    def test_exact_version_diff_and_mcp_allowlist(self):
        result = self.app.tools.compare_document_versions(
            "ownership-register", "1.0", "2.0", principal=self.alice)
        self.assertIn("-Atlas owner: Alice", result["diff"])
        listed = self.app.mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, self.alice)
        names = {t["name"] for t in listed["result"]["tools"]}
        self.assertEqual(set(self.app.tools.NAMES), names)
        denied = self.app.mcp.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "run_cypher", "arguments": {"query": "MATCH (n) RETURN n"}}},
            self.alice)
        self.assertIn("error", denied)

    def test_delta_cursor_and_idempotent_replay(self):
        before = len(self.app.store.records)
        cursor = self.app.ingester.replay()
        self.assertEqual("16", cursor)
        self.assertEqual(before, len(self.app.store.records))

    def test_gateway_content_source_preserves_source_contract(self):
        source = self.app.source

        class DirectGatewayClient:
            def call(self, tool_name, **arguments):
                return dispatch_source_gateway(source, tool_name, arguments)

        gateway_source = GatewayContentSource(DirectGatewayClient())
        self.assertEqual("16", gateway_source.changes()[1])
        self.assertEqual(
            ["1.0", "2.0"],
            [
                version.version_id
                for version in gateway_source.list_versions(
                    "ownership-register")
            ],
        )
        self.assertEqual(
            "Bob",
            gateway_source.get_current(
                "ownership-register").claims[0]["value"],
        )
        self.assertTrue(
            gateway_source.check_access("ownership-register", self.alice)
        )
        self.assertFalse(
            gateway_source.check_access("secret-plan", self.alice)
        )

    def test_gateway_source_tools_are_separate_from_agent_tools(self):
        self.assertEqual(
            set(SOURCE_GATEWAY_TOOL_NAMES),
            {tool["name"] for tool in SOURCE_GATEWAY_TOOLS},
        )
        self.assertTrue(
            set(SOURCE_GATEWAY_TOOL_NAMES).isdisjoint(self.app.tools.NAMES)
        )
        with self.assertRaisesRegex(ValueError, "unknown ContentSource"):
            dispatch_source_gateway(
                self.app.source,
                "query_temporal_graph",
                {},
            )

    def test_async_source_sync_uses_gateway_delta_and_exact_versions(self):
        source = self.app.source

        class DirectGatewayClient:
            def call(self, tool_name, **arguments):
                return dispatch_source_gateway(source, tool_name, arguments)

        class FakeStateTable:
            def __init__(self):
                self.item = {}

            def get_item(self, **_kwargs):
                return {"Item": self.item} if self.item else {}

            def put_item(self, Item):
                self.item = Item

        class FakeS3:
            def __init__(self):
                self.objects = []

            def put_object(self, **kwargs):
                self.objects.append(kwargs)

        class FakeSQS:
            def __init__(self):
                self.messages = []

            def send_message(self, **kwargs):
                self.messages.append(kwargs)

        state, storage, queue = FakeStateTable(), FakeS3(), FakeSQS()
        first = sync_once(
            GatewayContentSource(DirectGatewayClient()),
            state,
            storage,
            queue,
            "artifact-bucket",
            "queue-url",
            "mock-sharepoint",
        )
        second = sync_once(
            GatewayContentSource(DirectGatewayClient()),
            state,
            storage,
            queue,
            "artifact-bucket",
            "queue-url",
            "mock-sharepoint",
        )
        self.assertEqual(16, first["queued"])
        self.assertEqual("16", first["cursor"])
        self.assertEqual(0, second["queued"])
        self.assertEqual(16, len(storage.objects))
        self.assertEqual(16, len(queue.messages))
        self.assertEqual(
            "SOURCE_CURSOR#mock-sharepoint",
            state.item["pk"],
        )

    def test_gateway_mcp_client_decodes_json_and_sse(self):
        payload = {"jsonrpc": "2.0", "id": "1", "result": {"tools": []}}
        self.assertEqual(
            payload,
            GatewayMCPClient._decode_response(json.dumps(payload)),
        )
        self.assertEqual(
            payload,
            GatewayMCPClient._decode_response(
                f"event: message\ndata: {json.dumps(payload)}\n\n"
            ),
        )

    def test_gateway_mcp_client_sends_stateless_protocol_metadata(self):
        client = GatewayMCPClient(
            "https://gateway.example.test",
            "pool",
            "client",
            "https://token.example.test",
            "us-east-1",
        )
        client._access_token = lambda: "token"

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"tools": []},
                }).encode()

        with patch.object(
            urllib.request,
            "urlopen",
            return_value=FakeResponse(),
        ) as urlopen:
            client._request("tools/list", {})
        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(
            "2026-07-28",
            body["params"]["_meta"][
                "io.modelcontextprotocol/protocolVersion"
            ],
        )
        self.assertEqual("tools/list", request.headers["Mcp-method"])
        self.assertEqual(
            "2026-07-28",
            request.headers["Mcp-protocol-version"],
        )

    def test_gateway_deployment_payloads_match_sdk_contract(self):
        service = boto3.Session()._session.get_service_model(
            "bedrock-agentcore-control")
        account = "123456" * 2
        create_gateway = gateway_configuration(
            "sharepoint-temporal-agent-source",
            f"arn:aws:iam::{account}:role/gateway",
            "https://issuer.example.test/pool",
            "client-id",
        )
        create_target = {
            "gatewayIdentifier": "gateway-id",
            **target_configuration(
                f"arn:aws:lambda:us-east-1:{account}:"
                "function:mock-source"
            ),
        }
        validate_parameters(
            create_gateway,
            service.operation_model("CreateGateway").input_shape,
        )
        validate_parameters(
            create_target,
            service.operation_model("CreateGatewayTarget").input_shape,
        )
        self.assertEqual(
            ["2026-07-28"],
            create_gateway["protocolConfiguration"]["mcp"][
                "supportedVersions"
            ],
        )

    def test_added_phoenix_data_ingests_with_temporal_semantics(self):
        before = self.evidence(
            entity="Phoenix", relationship="owner",
            as_of="2025-02-01T00:00:00Z",
        )
        after = self.evidence(
            entity="Phoenix", relationship="owner",
            as_of="2025-08-01T00:00:00Z",
        )
        self.assertEqual(["Dana"], [row["value"] for row in before])
        self.assertEqual(["Erin"], [row["value"] for row in after])
        self.assertEqual(
            "2025-07-01T00:00:00Z",
            before[0]["validity"]["to"],
        )

        not_effective = self.evidence(
            entity="Phoenix Records", relationship="retention_period",
            as_of="2026-09-15T00:00:00Z",
        )
        effective = self.evidence(
            entity="Phoenix Records", relationship="retention_period",
            as_of="2026-10-02T00:00:00Z",
        )
        self.assertEqual([], not_effective)
        self.assertEqual(["5 years"], [row["value"] for row in effective])
        self.assertEqual(
            "2026-08-15T12:00:00Z",
            effective[0]["provenance"]["modified_at"],
        )

        risks = self.evidence(
            entity="Phoenix", relationship="risk",
            as_of="2025-04-01T00:00:00Z",
        )
        self.assertEqual({"Low", "High"}, {row["value"] for row in risks})

    def test_agentcore_custom_principal_headers(self):
        headers = {
            "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Principal-Id": "user-1",
            "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Principal-Groups":
                "risk,governance",
        }
        principal = principal_from_headers(headers)
        self.assertEqual("user-1", principal.id)
        self.assertEqual(
            frozenset({"risk", "governance"}),
            principal.groups,
        )

    def test_both_agentcore_runtimes_use_versioned_codezip(self):
        tools = build_code_artifact(
            "artifact-bucket", "runtime/runtime.zip", "version-1",
            "runtime_tools.py",
        )
        orchestrator = build_code_artifact(
            "artifact-bucket", "runtime/runtime.zip", "version-1",
            "runtime_agent.py",
        )
        self.assertNotIn("containerConfiguration", tools)
        self.assertEqual(
            ["runtime_tools.py"],
            tools["codeConfiguration"]["entryPoint"],
        )
        self.assertEqual(
            ["runtime_agent.py"],
            orchestrator["codeConfiguration"]["entryPoint"],
        )
        self.assertEqual(
            tools["codeConfiguration"]["code"],
            orchestrator["codeConfiguration"]["code"],
        )
        self.assertEqual("codeConfiguration", artifact_type(tools))

    def test_bedrock_agent_selects_allowlisted_tool(self):
        class FakeBedrock:
            def __init__(self):
                self.calls = 0

            def converse(self, **kwargs):
                self.calls += 1
                if self.calls <= 2:
                    names = {
                        tool["toolSpec"]["name"]
                        for tool in kwargs["toolConfig"]["tools"]
                    }
                    self.assert_names = names
                    return {"output": {"message": {
                        "role": "assistant",
                        "content": [{"toolUse": {
                            "toolUseId": f"tool-{self.calls}",
                            "name": "query_temporal_graph",
                            "input": {
                                "entity": "Phoenix",
                                "relationship": "owner",
                                "as_of": "2025-08-01",
                            },
                        }}],
                    }}}
                return {"output": {"message": {
                    "role": "assistant",
                    "content": [{"text": (
                        "Phoenix was owned by Erin with cited evidence."
                    )}],
                }}}

        client = FakeBedrock()
        model_agent = BedrockTemporalAgent(
            self.app.tools, model_id="fake-model", client=client)
        result = AgentOrchestrator(
            self.app.tools, agent=model_agent
        ).invoke({"prompt": "Who owned Phoenix on 2025-08-01?"}, self.alice)
        self.assertEqual(set(self.app.tools.NAMES), client.assert_names)
        self.assertEqual("Erin", result["evidence"][0]["value"])
        self.assertEqual(1, len(result["evidence"]))
        self.assertEqual(
            "2025-08-01T23:59:59Z",
            result["tool_trace"][0]["arguments"]["as_of"],
        )
        self.assertEqual("query_temporal_graph", result["tool_trace"][0]["tool"])

    def test_orchestrator_rejects_missing_prompt(self):
        orchestrator = AgentOrchestrator(self.app.tools, agent=object())
        with self.assertRaises(ValueError):
            orchestrator.invoke({}, self.alice)

    def test_runtime_builders_have_no_local_fallback(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(KeyError):
                build_orchestrator_runtime()
            with self.assertRaisesRegex(
                RuntimeError, "TEMPORAL_FACTS_TABLE is required"
            ):
                build_tools_runtime()
        with patch.dict(
            "os.environ",
            {"TEMPORAL_FACTS_TABLE": "temporal-facts"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "SOURCE_GATEWAY_URL is required"
            ):
                build_tools_runtime()


if __name__ == "__main__":
    unittest.main()
