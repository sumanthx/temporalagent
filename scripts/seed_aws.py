#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boto3
from temporal_agent.aws_backend import archive_fixtures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--skip-queue", action="store_true")
    args = parser.parse_args()
    keys = archive_fixtures(
        args.bucket, region=args.region, profile=args.profile)
    session = boto3.Session(
        profile_name=args.profile, region_name=args.region)
    sqs = session.client("sqs")
    with open("fixtures/changes.jsonl") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    if not args.skip_queue:
        for event in events:
            sqs.send_message(QueueUrl=args.queue_url, MessageBody=json.dumps(event),
                             MessageAttributes={"source": {"DataType": "String",
                                                           "StringValue": "mock-sharepoint"}})
    print(json.dumps({
        "archived_objects": len(keys),
        "queued_changes": 0 if args.skip_queue else len(events),
    }))


if __name__ == "__main__":
    main()
