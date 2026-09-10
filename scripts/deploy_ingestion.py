#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time

import boto3
from botocore.exceptions import ClientError


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--function-name", required=True)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--version-id")
    parser.add_argument("--queue-arn", required=True)
    parser.add_argument("--facts-table", required=True)
    parser.add_argument("--state-table", required=True)
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = session.client("lambda")
    code = {
        "S3Bucket": args.bucket,
        "S3Key": args.key,
        **({"S3ObjectVersion": args.version_id} if args.version_id else {}),
    }
    configuration = {
        "FunctionName": args.function_name,
        "Role": args.role_arn,
        "Handler": "aws_lambda.handlers.ingest",
        "Runtime": "python3.13",
        "Timeout": 60,
        "MemorySize": 256,
        "Architectures": ["arm64"],
        "Environment": {"Variables": {
            "TEMPORAL_FACTS_TABLE": args.facts_table,
            "SOURCE_STATE_TABLE": args.state_table,
            "ARTIFACT_BUCKET": args.bucket,
        }},
    }

    try:
        client.get_function(FunctionName=args.function_name)
    except client.exceptions.ResourceNotFoundException:
        for attempt in range(6):
            try:
                client.create_function(Code=code, **configuration)
                break
            except ClientError as exc:
                if attempt == 5 or "cannot be assumed" not in str(exc):
                    raise
                time.sleep(5)
        client.get_waiter("function_active_v2").wait(
            FunctionName=args.function_name)
    else:
        client.update_function_code(
            FunctionName=args.function_name,
            Publish=True,
            **code,
        )
        client.get_waiter("function_updated").wait(
            FunctionName=args.function_name)
        update_configuration = dict(configuration)
        update_configuration.pop("Architectures")
        client.update_function_configuration(**update_configuration)
        client.get_waiter("function_updated_v2").wait(
            FunctionName=args.function_name)

    client.put_function_concurrency(
        FunctionName=args.function_name,
        ReservedConcurrentExecutions=1,
    )
    mappings = client.list_event_source_mappings(
        EventSourceArn=args.queue_arn,
        FunctionName=args.function_name,
    )["EventSourceMappings"]
    if mappings:
        client.update_event_source_mapping(
            UUID=mappings[0]["UUID"],
            BatchSize=10,
            Enabled=True,
        )
    else:
        client.create_event_source_mapping(
            EventSourceArn=args.queue_arn,
            FunctionName=args.function_name,
            BatchSize=10,
            Enabled=True,
        )
    print(json.dumps({"function_name": args.function_name}))


if __name__ == "__main__":
    main()
