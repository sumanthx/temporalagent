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
    parser.add_argument("--artifact-bucket", required=True)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--state-table", required=True)
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--token-url", required=True)
    parser.add_argument("--source-id", default="mock-sharepoint")
    parser.add_argument("--schedule", default="rate(5 minutes)")
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
        "Handler": "aws_lambda.source_sync.handle",
        "Runtime": "python3.13",
        "Timeout": 120,
        "MemorySize": 256,
        "Architectures": ["arm64"],
        "Environment": {"Variables": {
            "AWS_REGION_NAME": args.region,
            "ARTIFACT_BUCKET": args.artifact_bucket,
            "CHANGE_QUEUE_URL": args.queue_url,
            "SOURCE_STATE_TABLE": args.state_table,
            "SOURCE_GATEWAY_URL": args.gateway_url,
            "SOURCE_GATEWAY_USER_POOL_ID": args.user_pool_id,
            "SOURCE_GATEWAY_CLIENT_ID": args.client_id,
            "SOURCE_GATEWAY_TOKEN_URL": args.token_url,
            "SOURCE_ID": args.source_id,
        }},
    }

    try:
        client.get_function(FunctionName=args.function_name)
    except client.exceptions.ResourceNotFoundException:
        for attempt in range(6):
            try:
                response = client.create_function(Code=code, **configuration)
                break
            except ClientError as exc:
                if attempt == 5 or "cannot be assumed" not in str(exc):
                    raise
                time.sleep(5)
        client.get_waiter("function_active_v2").wait(
            FunctionName=args.function_name)
        function_arn = response["FunctionArn"]
    else:
        response = client.update_function_code(
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
        function_arn = response["FunctionArn"]

    client.put_function_concurrency(
        FunctionName=args.function_name,
        ReservedConcurrentExecutions=1,
    )
    events = session.client("events")
    rule_name = f"{args.function_name}-schedule"
    rule = events.put_rule(
        Name=rule_name,
        ScheduleExpression=args.schedule,
        State="ENABLED",
        Description="Poll the SharePoint ContentSource delta through Gateway",
    )
    try:
        client.add_permission(
            FunctionName=args.function_name,
            StatementId="AllowScheduledSourceSync",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=rule["RuleArn"],
        )
    except client.exceptions.ResourceConflictException:
        pass
    failed = events.put_targets(
        Rule=rule_name,
        Targets=[{
            "Id": "source-sync",
            "Arn": function_arn,
        }],
    )["FailedEntryCount"]
    if failed:
        raise RuntimeError("failed to attach the source-sync schedule")

    invocation = client.invoke(
        FunctionName=args.function_name,
        InvocationType="RequestResponse",
        Payload=b"{}",
    )
    result = json.load(invocation["Payload"])
    if "FunctionError" in invocation:
        raise RuntimeError(f"source sync failed: {result}")
    print(json.dumps({
        "function_name": args.function_name,
        "function_arn": function_arn,
        "schedule_rule": rule_name,
        "initial_sync": result,
    }))


if __name__ == "__main__":
    main()
