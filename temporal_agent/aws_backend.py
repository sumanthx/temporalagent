from __future__ import annotations
import json
from decimal import Decimal
from typing import Any
try:
    import boto3
except ImportError:
    boto3 = None
from .store import TemporalGraphStore
from .models import TemporalRecord


class DynamoTemporalGraphStore(TemporalGraphStore):
    def __init__(self, table_name: str, region: str = "us-east-1"):
        if boto3 is None:
            raise RuntimeError("boto3 is required for AWS mode")
        super().__init__()
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def load_all(self) -> int:
        items = []
        response = self.table.scan()
        items.extend(response.get("Items", []))
        while response.get("LastEvaluatedKey"):
            response = self.table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))
        self.records = [
            TemporalRecord(
                record_id=item["record_id"], document_id=item["document_id"],
                version_id=item["version_id"], entity=item["entity"],
                relationship=item["relationship"], value=item["value"],
                valid_from=item["valid_from"], valid_to=item["valid_to"],
                recorded_from=item["recorded_from"], recorded_to=item["recorded_to"],
                source_path=item["source_path"], citation=item["citation"],
                provenance=item.get("provenance", {}),
                tombstone=bool(item.get("tombstone", False)),
            ) for item in items
        ]
        return len(self.records)

    def persist_all(self) -> int:
        with self.table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
            for r in self.records:
                item = r.evidence()
                item.update({
                    "pk": f"ENTITY#{r.entity}",
                    "sk": f"REL#{r.relationship}#VALID#{r.valid_from}#REC#{r.recorded_from}#{r.record_id}",
                    "record_id": r.record_id, "document_id": r.document_id,
                    "version_id": r.version_id, "entity": r.entity,
                    "relationship": r.relationship, "value": r.value,
                    "valid_from": r.valid_from, "valid_to": r.valid_to,
                    "recorded_from": r.recorded_from, "recorded_to": r.recorded_to,
                    "source_path": r.source_path, "citation": r.citation,
                    "tombstone": r.tombstone,
                })
                batch.put_item(Item=_ddb(item))
        return len(self.records)


def archive_fixtures(bucket: str, fixture_root: str = "fixtures") -> list[str]:
    if boto3 is None:
        raise RuntimeError("boto3 is required for AWS mode")
    s3 = boto3.client("s3")
    keys = []
    for filename in ("repository.json", "changes.jsonl"):
        key = f"mock-source/{filename}"
        s3.upload_file(f"{fixture_root}/{filename}", bucket, key)
        keys.append(key)
    with open(f"{fixture_root}/repository.json") as handle:
        repository = json.load(handle)
    for version in repository["versions"]:
        key = f"versions/{version['document_id']}/{version['version_id']}.json"
        s3.put_object(Bucket=bucket, Key=key,
                      Body=json.dumps(version, separators=(",", ":")).encode(),
                      ContentType="application/json",
                      Metadata={"document-id": version["document_id"],
                                "version-id": version["version_id"]})
        keys.append(key)
    current = {}
    for version in repository["versions"]:
        previous = current.get(version["document_id"])
        if previous is None or (version["valid_from"], version["version_id"]) > (
                previous["valid_from"], previous["version_id"]):
            current[version["document_id"]] = version
    for version in current.values():
        if version.get("deleted"):
            continue
        key = f"knowledge-base/current/{version['document_id']}.txt"
        body = (
            f"Title: {version['title']}\n"
            f"Document ID: {version['document_id']}\n"
            f"Version: {version['version_id']}\n"
            f"Path: {version['path']}\n"
            f"Valid from: {version['valid_from']}\n\n{version['content']}"
        )
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode(),
                      ContentType="text/plain")
        keys.append(key)
    return keys


def _ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ddb(v) for v in value]
    return value
