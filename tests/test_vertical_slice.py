import json
import unittest

from temporal_agent.app import build_demo
from temporal_agent.bedrock_agent import BedrockTemporalAgent
from temporal_agent.models import MAX_TIME, Principal


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
        self.assertEqual(set(self.app.tools.NAMES) - {"ask_temporal"}, names)
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

    def test_natural_language_tool(self):
        result = self.app.tools.ask_temporal(
            "Who owned Atlas on 2024-02-01?", principal=self.alice)
        self.assertIn("Alice", result["answer"])
        self.assertIn("sharepoint://ownership-register/versions/1.0",
                      result["answer"])
        self.assertEqual("2024-03-01T00:00:00Z",
                         result["evidence"][0]["validity"]["to"])

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
        self.assertNotIn("ask_temporal", client.assert_names)
        self.assertEqual("Alice", result["evidence"][0]["value"])
        self.assertEqual("query_temporal_graph", result["tool_trace"][0]["tool"])


if __name__ == "__main__":
    unittest.main()
