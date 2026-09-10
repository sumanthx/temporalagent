import hashlib
import json
import os
import boto3

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
MAX_TIME = "9999-12-31T23:59:59Z"


def _repair_valid_intervals(table, entity, relationship, document_id):
    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("pk").eq(
            f"ENTITY#{entity}"),
        ConsistentRead=True,
    )
    rows = [
        row for row in response.get("Items", [])
        if row["relationship"] == relationship
        and row["document_id"] == document_id
        and not row.get("tombstone", False)
    ]
    starts = sorted({row["valid_from"] for row in rows})
    for row in rows:
        later = [start for start in starts if start > row["valid_from"]]
        valid_to = min(later, default=MAX_TIME)
        if row["valid_to"] != valid_to:
            table.update_item(
                Key={"pk": row["pk"], "sk": row["sk"]},
                UpdateExpression="SET valid_to = :valid_to",
                ExpressionAttributeValues={":valid_to": valid_to},
            )


def ingest(event, _context):
    facts = ddb.Table(os.environ["TEMPORAL_FACTS_TABLE"])
    state = ddb.Table(os.environ["SOURCE_STATE_TABLE"])
    bucket = os.environ["ARTIFACT_BUCKET"]
    processed = 0
    for message in event.get("Records", []):
        change = json.loads(message["body"])
        event_id = change["event_id"]
        event_key = {"pk": f"EVENT#{event_id}"}
        if state.get_item(Key=event_key, ConsistentRead=True).get("Item"):
            continue
        key = f"versions/{change['document_id']}/{change['version_id']}.json"
        version = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        claims = list(version.get("claims", []))
        if version.get("deleted"):
            claims.append({
                "entity": version["document_id"],
                "relationship": "status",
                "value": "deleted",
            })
        for claim in claims:
            raw = ":".join([
                version["document_id"], version["version_id"], claim["entity"],
                claim["relationship"], str(claim["value"]),
            ])
            record_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
            valid_from = claim.get("valid_from", version["valid_from"])
            item = {
                "pk": f"ENTITY#{claim['entity']}",
                "sk": f"REL#{claim['relationship']}#VALID#{valid_from}#"
                      f"REC#{change['recorded_at']}#{record_id}",
                "record_id": record_id,
                "document_id": version["document_id"],
                "version_id": version["version_id"],
                "entity": claim["entity"],
                "relationship": claim["relationship"],
                "value": str(claim["value"]),
                "valid_from": valid_from,
                "valid_to": MAX_TIME,
                "recorded_from": change["recorded_at"],
                "recorded_to": MAX_TIME,
                "source_path": version["path"],
                "citation": f"sharepoint://{version['document_id']}/versions/{version['version_id']}",
                "provenance": {
                    **version.get("provenance", {}),
                    "modified_at": version["modified_at"],
                },
                "tombstone": bool(version.get("deleted", False)),
            }
            try:
                facts.put_item(
                    Item=item,
                    ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
                )
            except ddb.meta.client.exceptions.ConditionalCheckFailedException:
                pass
            _repair_valid_intervals(
                facts,
                claim["entity"],
                claim["relationship"],
                version["document_id"],
            )
        try:
            state.put_item(
                Item={**event_key, "change": change},
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ddb.meta.client.exceptions.ConditionalCheckFailedException:
            pass
        processed += 1
    return {"processed": processed}
