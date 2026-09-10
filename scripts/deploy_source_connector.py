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
        "Handler": "aws_lambda.source_gateway.handle",
        "Runtime": "python3.13",
        "Timeout": 60,
        "MemorySize": 256,
        "Architectures": ["arm64"],
        "Environment": {"Variables": {
            "ARTIFACT_BUCKET": args.artifact_bucket,
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

    print(json.dumps({
        "function_name": args.function_name,
        "function_arn": function_arn,
    }))


if __name__ == "__main__":
    main()
