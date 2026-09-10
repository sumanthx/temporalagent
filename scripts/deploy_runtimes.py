#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

import boto3


def build_code_artifact(
    bucket: str,
    key: str,
    version_id: str | None,
    entry_point: str,
) -> dict:
    return {
        "codeConfiguration": {
            "code": {"s3": {
                "bucket": bucket,
                "prefix": key,
                **({"versionId": version_id} if version_id else {}),
            }},
            "runtime": "PYTHON_3_13",
            "entryPoint": [entry_point],
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--account", required=True)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--version-id")
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--token-url", required=True)
    parser.add_argument("--table", required=True)
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = session.client("bedrock-agentcore-control")
    runtimes = {
        runtime["agentRuntimeName"]: runtime
        for page in client.get_paginator("list_agent_runtimes").paginate()
        for runtime in page["agentRuntimes"]
    }

    authorizer = {"customJWTAuthorizer": {
        "discoveryUrl": f"{args.issuer}/.well-known/openid-configuration",
        "allowedClients": [args.client_id],
    }}

    def upsert(name: str, description: str, protocol: str,
               runtime_artifact: dict, environment: dict[str, str]) -> str:
        common = {
            "description": description,
            "agentRuntimeArtifact": runtime_artifact,
            "roleArn": args.role_arn,
            "networkConfiguration": {"networkMode": "PUBLIC"},
            "protocolConfiguration": {"serverProtocol": protocol},
            "environmentVariables": environment,
            "authorizerConfiguration": authorizer,
            "requestHeaderConfiguration": {
                "requestHeaderAllowlist": [
                    "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Principal-Id",
                    "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Principal-Groups",
                ]
            },
        }
        existing = runtimes.get(name)
        if existing:
            client.update_agent_runtime(
                agentRuntimeId=existing["agentRuntimeId"],
                **common,
            )
            return existing["agentRuntimeId"]
        response = client.create_agent_runtime(agentRuntimeName=name, **common)
        return response["agentRuntimeId"]

    tools_id = upsert(
        "sharepoint_temporal_tools",
        "SharePoint temporal structured MCP tools",
        "MCP",
        build_code_artifact(
            args.bucket, args.key, args.version_id, "runtime_tools.py"
        ),
        {
            "AWS_REGION": args.region,
            "RUNTIME_KIND": "tools",
            "TEMPORAL_FACTS_TABLE": args.table,
        },
    )
    tools_arn = (
        f"arn:aws:bedrock-agentcore:{args.region}:{args.account}:"
        f"runtime/{tools_id}"
    )
    orchestrator_id = upsert(
        "sharepoint_temporal_orchestrator",
        "Natural-language Bedrock orchestrator over temporal MCP tools",
        "HTTP",
        build_code_artifact(
            args.bucket, args.key, args.version_id, "runtime_agent.py"
        ),
        {
            "AWS_REGION": args.region,
            "RUNTIME_KIND": "orchestrator",
            "BEDROCK_AGENT_MODEL_ID": "amazon.nova-lite-v1:0",
            "MCP_RUNTIME_ARN": tools_arn,
            "MCP_USER_POOL_ID": args.user_pool_id,
            "MCP_CLIENT_ID": args.client_id,
            "MCP_TOKEN_URL": args.token_url,
        },
    )
    print(json.dumps({
        "tools_runtime_id": tools_id,
        "orchestrator_runtime_id": orchestrator_id,
    }))


if __name__ == "__main__":
    main()
