from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from typing import Any


class GatewayMCPClient:
    """Authenticated MCP client for the permanent ContentSource Gateway."""

    PROTOCOL_VERSION = "2026-07-28"

    def __init__(
        self,
        gateway_url: str,
        user_pool_id: str,
        client_id: str,
        token_url: str,
        region: str,
    ):
        self.gateway_url = gateway_url.rstrip("/")
        if not self.gateway_url.endswith("/mcp"):
            self.gateway_url += "/mcp"
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.token_url = token_url
        self.region = region
        self._token: str | None = None
        self._expires_at = 0.0
        self._tools: dict[str, str] | None = None

    def call(self, tool_name: str, **arguments) -> dict[str, Any]:
        full_name = self._resolve_tool(tool_name)
        payload = self._request(
            "tools/call",
            {"name": full_name, "arguments": arguments},
        )
        result = payload["result"]
        if "structuredContent" in result:
            return result["structuredContent"]
        for item in result.get("content", []):
            if item.get("type") == "text":
                return json.loads(item["text"])
        raise RuntimeError(f"Gateway tool {full_name} returned no JSON content")

    def _resolve_tool(self, tool_name: str) -> str:
        if self._tools is None:
            result = self._request("tools/list", {})["result"]
            self._tools = {}
            for tool in result.get("tools", []):
                full_name = tool["name"]
                suffix = full_name.rsplit("___", 1)[-1]
                self._tools[suffix] = full_name
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise KeyError(
                f"Gateway source tool is unavailable: {tool_name}"
            ) from exc

    def _request(self, method: str, params: dict) -> dict[str, Any]:
        request_params = {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion":
                    self.PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "sharepoint-temporal-tools",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        }
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.PROTOCOL_VERSION,
            "Mcp-Method": method,
        }
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        request = urllib.request.Request(
            self.gateway_url,
            data=json.dumps({
                "jsonrpc": "2.0",
                "id": str(time.time_ns()),
                "method": method,
                "params": request_params,
            }).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
        payload = self._decode_response(raw)
        if "error" in payload:
            raise RuntimeError(payload["error"]["message"])
        return payload

    @staticmethod
    def _decode_response(raw: str) -> dict[str, Any]:
        stripped = raw.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)
        data_lines = [
            line.removeprefix("data:").strip()
            for line in stripped.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            raise RuntimeError("Gateway returned an unsupported MCP response")
        return json.loads(data_lines[-1])

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
            f"{self.client_id}:{details['ClientSecret']}".encode()
        ).decode()
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
