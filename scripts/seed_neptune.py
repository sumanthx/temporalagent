#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporal_agent.app import build_demo


def run_query(graph_id, region, profile, query):
    subprocess.run([
        "aws", "neptune-graph", "execute-query",
        "--graph-identifier", graph_id,
        "--language", "OPEN_CYPHER",
        "--query-string", query,
        "--region", region, "--profile", profile,
        "/tmp/sharepoint-temporal-neptune-query.json",
    ], check=True, stdout=subprocess.DEVNULL)


def literal(value):
    return json.dumps(str(value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default="default")
    args = parser.parse_args()
    app = build_demo()
    for record in app.store.records:
        query = (
            f"MERGE (e:Entity {{name:{literal(record.entity)}}}) "
            f"MERGE (d:Document {{id:{literal(record.document_id)}}}) "
            f"MERGE (v:Version {{id:{literal(record.document_id + ':' + record.version_id)}}}) "
            f"SET v.versionId={literal(record.version_id)}, "
            f"v.path={literal(record.source_path)}, v.citation={literal(record.citation)} "
            f"MERGE (d)-[:HAS_VERSION]->(v) "
            f"MERGE (e)-[r:{record.relationship.upper()} "
            f"{{recordId:{literal(record.record_id)}}}]->(v) "
            f"SET r.value={literal(record.value)}, r.validFrom={literal(record.valid_from)}, "
            f"r.validTo={literal(record.valid_to)}, r.recordedFrom={literal(record.recorded_from)}, "
            f"r.recordedTo={literal(record.recorded_to)}, r.tombstone={str(record.tombstone).lower()}"
        )
        run_query(args.graph_id, args.region, args.profile, query)
    print(json.dumps({"graph_id": args.graph_id, "facts_seeded": len(app.store.records)}))


if __name__ == "__main__":
    main()
