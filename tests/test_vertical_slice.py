import unittest

from temporal_agent.app import build_demo
from temporal_agent.bedrock_agent import BedrockTemporalAgent
from temporal_agent.models import MAX_TIME, Principal
from temporal_agent.server import principal_from_headers
from aws_lambda.handlers import _repair_valid_intervals
from scripts.deploy_runtimes import build_code_artifact


class VerticalSliceTests(unittest.TestCase):
    def setUp(self):
        self.app = build_demo()
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
        self.assertEqual("11", cursor)
        self.assertEqual(before, len(self.app.store.records))

    def test_natural_language_orchestrator(self):
        result = self.app.orchestrator.invoke(
            {"prompt": "Who owned Atlas on 2024-02-01?"},
            principal=self.alice,
        )
        self.assertIn("Alice", result["answer"])
        self.assertIn("sharepoint://ownership-register/versions/1.0",
                      result["answer"])
        self.assertEqual("2024-03-01T00:00:00Z",
                         result["evidence"][0]["validity"]["to"])

    def test_documented_orchestrator_prompts(self):
        owned = self.app.orchestrator.invoke(
            {"prompt": "Who owned Atlas on 2024-02-01?"}, self.alice)
        self.assertEqual(["Alice"], [e["value"] for e in owned["evidence"]])

        not_effective = self.app.orchestrator.invoke(
            {"prompt": (
                "What was the retention policy about Customer Records "
                "on 2024-03-15?"
            )},
            self.alice,
        )
        self.assertEqual([], not_effective["evidence"])

        effective = self.app.orchestrator.invoke(
            {"prompt": (
                "What was the retention policy about Customer Records "
                "on 2024-04-02?"
            )},
            self.alice,
        )
        self.assertEqual(["7 years"], [
            e["value"] for e in effective["evidence"]
        ])

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

    def test_bedrock_agent_selects_allowlisted_tool(self):
        class FakeBedrock:
            def __init__(self):
                self.calls = 0

            def converse(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    names = {
                        tool["toolSpec"]["name"]
                        for tool in kwargs["toolConfig"]["tools"]
                    }
                    self.assert_names = names
                    return {"output": {"message": {
                        "role": "assistant",
                        "content": [{"toolUse": {
                            "toolUseId": "tool-1",
                            "name": "query_temporal_graph",
                            "input": {
                                "entity": "Atlas",
                                "relationship": "owner",
                                "as_of": "2024-02-01T23:59:59Z",
                            },
                        }}],
                    }}}
                return {"output": {"message": {
                    "role": "assistant",
                    "content": [{"text": "Atlas was owned by Alice with cited evidence."}],
                }}}

        client = FakeBedrock()
        result = BedrockTemporalAgent(
            self.app.tools, model_id="fake-model", client=client
        ).ask("Who owned Atlas on 2024-02-01?", self.alice)
        self.assertEqual(set(self.app.tools.NAMES), client.assert_names)
        self.assertEqual("Alice", result["evidence"][0]["value"])
        self.assertEqual("query_temporal_graph", result["tool_trace"][0]["tool"])


if __name__ == "__main__":
    unittest.main()
