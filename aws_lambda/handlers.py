import hashlib
import json
import os
import boto3

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")


def ingest(event, _context):
    facts = ddb.Table(os.environ["TEMPORAL_FACTS_TABLE"])
    state = ddb.Table(os.environ["SOURCE_STATE_TABLE"])
    bucket = os.environ["ARTIFACT_BUCKET"]
    processed = 0
    for message in event.get("Records", []):
        change = json.loads(message["body"])
        event_id = change["event_id"]
        try:
            state.put_item(
                Item={"pk": f"EVENT#{event_id}", "change": change},
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ddb.meta.client.exceptions.ConditionalCheckFailedException:
            continue
        key = f"versions/{change['document_id']}/{change['version_id']}.json"
        version = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        for claim in version.get("claims", []):
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
                "valid_to": "9999-12-31T23:59:59Z",
                "recorded_from": change["recorded_at"],
                "recorded_to": "9999-12-31T23:59:59Z",
                "source_path": version["path"],
                "citation": f"sharepoint://{version['document_id']}/versions/{version['version_id']}",
                "provenance": version.get("provenance", {}),
                "tombstone": bool(version.get("deleted", False)),
            }
            try:
                facts.put_item(
                    Item=item,
                    ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
                )
            except ddb.meta.client.exceptions.ConditionalCheckFailedException:
                pass
        processed += 1
    return {"processed": processed}


def gateway(event, _context):
    args = (event.get("arguments") or event.get("input")
            or event.get("parameters") or event)
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        args = {row["name"]: row.get("value") for row in args}
    if not args and event.get("requestBody"):
        content = event["requestBody"].get("content", {})
        media = content.get("application/json") or next(iter(content.values()), {})
        properties = media.get("properties", [])
        args = {row["name"]: row.get("value") for row in properties}
    entity = args.get("entity")
    relationship = args.get("relationship")
    as_of = args.get("as_of")
    table = ddb.Table(os.environ["TEMPORAL_FACTS_TABLE"])
    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("pk").eq(f"ENTITY#{entity}")
    )
    rows = response.get("Items", [])
    if relationship:
        rows = [r for r in rows if r["relationship"] == relationship]
    if as_of:
        rows = [r for r in rows if r["valid_from"] <= as_of < r["valid_to"]]
    context = event.get("requestContext", {})
    claims = context.get("authorizer", {}).get("claims", {})
    principal = claims.get("sub")
    if not principal:
        return {"evidence": [], "access_denied": True,
                "reason": "verified caller identity required"}
    repository = json.loads(s3.get_object(
        Bucket=os.environ["ARTIFACT_BUCKET"],
        Key="mock-source/repository.json")["Body"].read())
    current = {}
    for version in repository["versions"]:
        prior = current.get(version["document_id"])
        if prior is None or (version["valid_from"], version["version_id"]) > (
                prior["valid_from"], prior["version_id"]):
            current[version["document_id"]] = version
    rows = [
        r for r in rows
        if not current.get(r["document_id"], {}).get("deleted", False)
        and principal in current.get(r["document_id"], {}).get("readers", [])
    ]
    return {
        "evidence": [{
            "entity": r["entity"], "relationship": r["relationship"], "value": r["value"],
            "validity": {"from": r["valid_from"], "to": r["valid_to"]},
            "source": {"artifact": r["source_path"], "document_id": r["document_id"],
                       "version_id": r["version_id"]},
            "citation": r["citation"],
        } for r in rows]
    }
