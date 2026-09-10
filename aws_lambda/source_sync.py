from __future__ import annotations

import json
import os
from typing import Any

import boto3

from temporal_agent.gateway_client import GatewayMCPClient
from temporal_agent.source import GatewayContentSource, source_version_to_dict


def sync_once(
    source: GatewayContentSource,
    state_table: Any,
    s3_client: Any,
    sqs_client: Any,
    bucket: str,
    queue_url: str,
    source_id: str,
) -> dict[str, Any]:
    cursor_key = {"pk": f"SOURCE_CURSOR#{source_id}"}
    item = state_table.get_item(
        Key=cursor_key,
        ConsistentRead=True,
    ).get("Item", {})
    old_cursor = item.get("cursor")
    events, new_cursor = source.changes(old_cursor)
    queued = 0
    for event in events:
        version = source.get_version(
            event["document_id"],
            event["version_id"],
        )
        s3_client.put_object(
            Bucket=bucket,
            Key=(
                f"versions/{version.document_id}/{version.version_id}.json"
            ),
            Body=json.dumps(
                source_version_to_dict(version),
                separators=(",", ":"),
            ).encode(),
            ContentType="application/json",
            Metadata={
                "document-id": version.document_id,
                "version-id": version.version_id,
            },
        )
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(event, separators=(",", ":")),
            MessageAttributes={
                "source": {
                    "DataType": "String",
                    "StringValue": source_id,
                },
            },
        )
        queued += 1
    state_table.put_item(Item={
        **cursor_key,
        "cursor": new_cursor,
    })
    return {
        "source_id": source_id,
        "previous_cursor": old_cursor,
        "cursor": new_cursor,
        "queued": queued,
    }


def handle(_event: dict[str, Any], _context: Any) -> dict[str, Any]:
    region = os.environ.get(
        "AWS_REGION_NAME",
        os.environ.get("AWS_REGION", "us-east-1"),
    )
    source = GatewayContentSource(GatewayMCPClient(
        gateway_url=os.environ["SOURCE_GATEWAY_URL"],
        user_pool_id=os.environ["SOURCE_GATEWAY_USER_POOL_ID"],
        client_id=os.environ["SOURCE_GATEWAY_CLIENT_ID"],
        token_url=os.environ["SOURCE_GATEWAY_TOKEN_URL"],
        region=region,
    ))
    ddb = boto3.resource("dynamodb", region_name=region)
    return sync_once(
        source=source,
        state_table=ddb.Table(os.environ["SOURCE_STATE_TABLE"]),
        s3_client=boto3.client("s3", region_name=region),
        sqs_client=boto3.client("sqs", region_name=region),
        bucket=os.environ["ARTIFACT_BUCKET"],
        queue_url=os.environ["CHANGE_QUEUE_URL"],
        source_id=os.environ.get("SOURCE_ID", "mock-sharepoint"),
    )
