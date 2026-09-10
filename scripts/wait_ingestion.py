#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time

import boto3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--state-table", required=True)
    parser.add_argument("--expected-events", required=True, type=int)
    parser.add_argument("--timeout", default=180, type=int)
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    table = session.resource("dynamodb").Table(args.state_table)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        response = table.scan(
            Select="COUNT",
            FilterExpression="begins_with(pk, :prefix)",
            ExpressionAttributeValues={":prefix": "EVENT#"},
        )
        if response["Count"] >= args.expected_events:
            print(f"processed_events={response['Count']}")
            return
        time.sleep(3)
    raise TimeoutError(
        f"ingestion did not reach {args.expected_events} events "
        f"within {args.timeout} seconds"
    )


if __name__ == "__main__":
    main()
