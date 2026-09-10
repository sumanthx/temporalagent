#!/usr/bin/env python3
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from temporal_agent.app import build_demo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = build_demo()
    vertices = {}
    edges = []
    for r in app.store.records:
        entity_id = f"entity:{r.entity}"
        document_id = f"document:{r.document_id}"
        version_id = f"version:{r.document_id}:{r.version_id}"
        vertices[entity_id] = [entity_id, "Entity", r.entity, "", "", ""]
        vertices[document_id] = [document_id, "Document", "", r.document_id, "", ""]
        vertices[version_id] = [
            version_id, "Version", "", r.document_id, r.version_id, r.citation]
        edges.append([
            f"fact:{r.record_id}", entity_id, version_id, r.relationship,
            r.value, r.valid_from, r.valid_to, r.recorded_from, r.recorded_to,
            str(r.tombstone).lower(),
        ])
        edges.append([
            f"has:{r.document_id}:{r.version_id}", document_id, version_id,
            "HAS_VERSION", "", "", "", "", "", "false",
        ])
    with (args.output / "vertices.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["~id", "~label", "name:String", "documentId:String",
                         "versionId:String", "citation:String"])
        writer.writerows(vertices.values())
    with (args.output / "edges.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["~id", "~from", "~to", "~label", "value:String",
                         "validFrom:String", "validTo:String", "recordedFrom:String",
                         "recordedTo:String", "tombstone:Bool"])
        writer.writerows(edges)
    print(f"vertices={len(vertices)} edges={len(edges)}")


if __name__ == "__main__":
    main()
