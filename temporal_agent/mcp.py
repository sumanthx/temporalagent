from __future__ import annotations

from .models import Principal
from .tools import SharePointTools


class MCPService:
    def __init__(self, tools: SharePointTools):
        self.tools = tools

    def handle(self, request: dict, principal: Principal) -> dict:
        request_id = request.get("id")
        try:
            method = request["method"]
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18",
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "mock-sharepoint", "version": "0.1.0"}}
            elif method == "tools/list":
                result = {"tools": [
                    schema for schema in self.tools.schemas()
                    if schema["name"] != "ask_temporal"
                ]}
            elif method == "tools/call":
                params = request["params"]
                if params["name"] == "ask_temporal":
                    raise ValueError("orchestration is not exposed by the tools runtime")
                value = self.tools.call(params["name"], params.get("arguments", {}), principal)
                result = {"content": [{"type": "text", "text": __import__("json").dumps(value)}],
                          "structuredContent": value}
            else:
                raise ValueError(f"unsupported MCP method: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32000, "message": str(exc)}}
