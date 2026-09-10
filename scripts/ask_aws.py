#!/usr/bin/env python3
"""One-command natural-language demo for the deployed AgentCore runtime."""
import argparse
import json
import subprocess
import sys

import boto3


def discover(profile: str, region: str, stack_name: str) -> dict[str, str]:
    session = boto3.Session(profile_name=profile, region_name=region)
    cloudformation = session.client("cloudformation")
    stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = {
        item["OutputKey"]: item["OutputValue"]
        for item in stack.get("Outputs", [])
    }
    agentcore = session.client("bedrock-agentcore-control")
    runtimes = agentcore.list_agent_runtimes()["agentRuntimes"]
    runtime = next(
        item for item in runtimes
        if item["agentRuntimeName"] == "sharepoint_temporal_orchestrator"
    )
    details = agentcore.get_agent_runtime(
        agentRuntimeId=runtime["agentRuntimeId"])
    return {
        "runtime_arn": details["agentRuntimeArn"],
        "user_pool_id": outputs["AgentUserPoolId"],
        "client_id": outputs["AgentMachineClientId"],
        "token_url": outputs["AgentTokenUrl"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack-name", default="sharepoint-temporal-agent-core")
    args = parser.parse_args()
    deployment = discover(args.profile, args.region, args.stack_name)
    request = {"prompt": args.question}
    command = [
        sys.executable, "scripts/invoke_agentcore.py",
        "--runtime-arn", deployment["runtime_arn"],
        "--user-pool-id", deployment["user_pool_id"],
        "--client-id", deployment["client_id"],
        "--token-url", deployment["token_url"],
        "--profile", args.profile,
        "--region", args.region,
        "--protocol", "http",
        "--request", json.dumps(request),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    response = json.loads(completed.stdout)
    print(response["answer"])


if __name__ == "__main__":
    main()
