#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from typing import Any

import boto3

from temporal_agent.gateway_contract import SOURCE_GATEWAY_TOOLS


def gateway_configuration(
    name: str,
    role_arn: str,
    issuer: str,
    client_id: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": (
            "Permanent MCP ContentSource boundary for SharePoint connectivity"
        ),
        "roleArn": role_arn,
        "protocolType": "MCP",
        "protocolConfiguration": {"mcp": {
            "supportedVersions": ["2026-07-28"],
            "instructions": (
                "Source connector operations for change ingestion, immutable "
                "versions, current search, and current authorization."
            ),
        }},
        "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": {"customJWTAuthorizer": {
            "discoveryUrl": (
                f"{issuer.rstrip('/')}/.well-known/openid-configuration"
            ),
            "allowedClients": [client_id],
        }},
    }


def target_configuration(
    function_arn: str,
) -> dict[str, Any]:
    return {
        "name": "sharepoint_source",
        "description": (
            "Swappable SharePoint ContentSource implementation; mock target now"
        ),
        "targetConfiguration": {"mcp": {"lambda": {
            "lambdaArn": function_arn,
            "toolSchema": {"inlinePayload": SOURCE_GATEWAY_TOOLS},
        }}},
        "credentialProviderConfigurations": [{
            "credentialProviderType": "GATEWAY_IAM_ROLE",
        }],
    }


def _wait_gateway(client: Any, gateway_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        details = client.get_gateway(gatewayIdentifier=gateway_id)
        status = details["status"]
        if status == "READY":
            return details
        if status in {"FAILED", "UPDATE_UNSUCCESSFUL"}:
            raise RuntimeError(
                f"gateway {gateway_id} entered status {status}: "
                f"{details.get('statusReasons', [])}"
            )
        time.sleep(5)
    raise TimeoutError(f"gateway {gateway_id} was not ready within 600 seconds")


def _wait_target(
    client: Any,
    gateway_id: str,
    target_id: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        details = client.get_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
        )
        status = details["status"]
        if status == "READY":
            return details
        if status in {
            "FAILED",
            "UPDATE_UNSUCCESSFUL",
            "SYNCHRONIZE_UNSUCCESSFUL",
        }:
            raise RuntimeError(
                f"gateway target {target_id} entered status {status}: "
                f"{details.get('statusReasons', [])}"
            )
        time.sleep(5)
    raise TimeoutError(
        f"gateway target {target_id} was not ready within 600 seconds")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--gateway-name", required=True)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--function-arn", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--client-id", required=True)
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = session.client("bedrock-agentcore-control")
    gateways = {
        gateway["name"]: gateway
        for page in client.get_paginator("list_gateways").paginate()
        for gateway in page["items"]
    }
    common = gateway_configuration(
        args.gateway_name,
        args.role_arn,
        args.issuer,
        args.client_id,
    )
    existing = gateways.get(args.gateway_name)
    if existing:
        gateway_id = existing["gatewayId"]
        client.update_gateway(
            gatewayIdentifier=gateway_id,
            **common,
        )
    else:
        response = client.create_gateway(**common)
        gateway_id = response["gatewayId"]
    gateway = _wait_gateway(client, gateway_id)

    targets = {
        target["name"]: target
        for page in client.get_paginator("list_gateway_targets").paginate(
            gatewayIdentifier=gateway_id,
        )
        for target in page["items"]
    }
    target_common = target_configuration(args.function_arn)
    existing_target = targets.get(target_common["name"])
    if existing_target:
        target_id = existing_target["targetId"]
        client.update_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
            **target_common,
        )
    else:
        response = client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            **target_common,
        )
        target_id = response["targetId"]
    _wait_target(client, gateway_id, target_id)

    print(json.dumps({
        "gateway_id": gateway_id,
        "gateway_url": gateway["gatewayUrl"],
        "target_id": target_id,
    }))


if __name__ == "__main__":
    main()
