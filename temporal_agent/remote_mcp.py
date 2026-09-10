from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request

from .models import Principal
from .tools import SharePointTools


class RemoteMCPTools:
    """Tool facade used by the orchestrator; all calls cross the MCP runtime."""

    def __init__(self):
        self.runtime_arn = os.environ["MCP_RUNTIME_ARN"]
        self.region = os.environ.get("AWS_REGION", "us-east-1")
        self.user_pool_id = os.environ["MCP_USER_POOL_ID"]
        self.client_id = os.environ["MCP_CLIENT_ID"]
        self.token_url = os.environ["MCP_TOKEN_URL"]
        self._token: str | None = None
        self._expires_at = 0.0

    def schemas(self):
        return SharePointTools.schemas()

    def call(self, name: str, arguments: dict, principal: Principal) -> dict:
        allowed = {schema["name"] for schema in self.schemas()}
        if name not in allowed:
            raise ValueError(f"remote MCP tool not allowed: {name}")
        request = {
            "jsonrpc": "2.0",
            "id": f"{time.time_ns()}",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        encoded = urllib.parse.quote(self.runtime_arn, safe="")
        endpoint = (
            f"https://bedrock-agentcore.{self.region}.amazonaws.com/"
            f"runtimes/{encoded}/invocations?qualifier=DEFAULT"
        )
        response = urllib.request.urlopen(urllib.request.Request(
            endpoint,
            data=json.dumps(request).encode(),
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
                "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Principal-Id": principal.id,
                "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Principal-Groups":
                    ",".join(sorted(principal.groups)),
            },
        ), timeout=60)
        payload = json.load(response)
        if "error" in payload:
            raise RuntimeError(payload["error"]["message"])
        return payload["result"]["structuredContent"]

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        import boto3
        client = boto3.client("cognito-idp", region_name=self.region)
        details = client.describe_user_pool_client(
            UserPoolId=self.user_pool_id,
            ClientId=self.client_id,
        )["UserPoolClient"]
        basic = base64.b64encode(
            f"{self.client_id}:{details['ClientSecret']}".encode()).decode()
        request = urllib.request.Request(
            self.token_url,
            data=urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "scope": "temporal-agent/invoke",
            }).encode(),
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            token = json.load(response)
        self._token = token["access_token"]
        self._expires_at = time.time() + int(token.get("expires_in", 3600))
        return self._token
