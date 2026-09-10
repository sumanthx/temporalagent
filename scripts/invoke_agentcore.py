#!/usr/bin/env python3
"""Obtain a Cognito client-credentials token and invoke AgentCore without logging secrets."""
import argparse
import base64
import json
import urllib.parse
import urllib.request

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-arn")
    parser.add_argument("--endpoint", help="Direct AgentCore Gateway MCP URL")
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--token-url", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--protocol", choices=["mcp", "http"], default="mcp")
    parser.add_argument("--principal", default="alice")
    parser.add_argument(
        "--request",
        default='{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
        help="MCP JSON-RPC request",
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = session.client("cognito-idp").describe_user_pool_client(
        UserPoolId=args.user_pool_id, ClientId=args.client_id)["UserPoolClient"]
    credentials = base64.b64encode(
        f"{args.client_id}:{client['ClientSecret']}".encode()).decode()
    token_request = urllib.request.Request(
        args.token_url,
        data=urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "scope": "temporal-agent/invoke",
        }).encode(),
        headers={"Authorization": f"Basic {credentials}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(token_request) as response:
        token = json.load(response)["access_token"]

    if args.endpoint:
        endpoint = args.endpoint
    else:
        if not args.runtime_arn:
            parser.error("--runtime-arn or --endpoint is required")
        encoded_arn = urllib.parse.quote(args.runtime_arn, safe="")
        endpoint = (
            f"https://bedrock-agentcore.{args.region}.amazonaws.com/"
            f"runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
        )
    payload = json.loads(args.request)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Principal-Id": args.principal,
    }
    if args.protocol == "mcp":
        headers["MCP-Protocol-Version"] = "2025-06-18"
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        print(response.read().decode())


if __name__ == "__main__":
    main()
