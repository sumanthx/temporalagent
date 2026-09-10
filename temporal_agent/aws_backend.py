from __future__ import annotations
import json
try:
    import boto3
except ImportError:
    boto3 = None
from .store import TemporalGraphStore
from .models import TemporalRecord


class DynamoTemporalGraphStore(TemporalGraphStore):
    def __init__(self, table_name: str, region: str = "us-east-1",
                 profile: str | None = None):
        if boto3 is None:
            raise RuntimeError("boto3 is required for AWS mode")
        super().__init__()
        session = boto3.Session(profile_name=profile, region_name=region)
        self.table = session.resource("dynamodb").Table(table_name)

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

    def refresh(self) -> None:
        self.load_all()

def archive_fixtures(bucket: str, fixture_root: str = "fixtures",
                     region: str = "us-east-1",
                     profile: str | None = None) -> list[str]:
    if boto3 is None:
        raise RuntimeError("boto3 is required for AWS mode")
    session = boto3.Session(profile_name=profile, region_name=region)
    s3 = session.client("s3")
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
    return keys
