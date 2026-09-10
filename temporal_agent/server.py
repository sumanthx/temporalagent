from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .models import Principal


def principal_from_headers(headers) -> Principal:
    return Principal(
        headers.get(
            "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Principal-Id",
            "alice",
        ),
        frozenset(filter(None, headers.get(
            "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Principal-Groups",
            "",
        ).split(","))),
    )


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/ping":
                self._send(200, {"status": "Healthy"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            principal = principal_from_headers(self.headers)
            if self.path == "/invocations":
                self._send(200, app.orchestrator.invoke(body, principal))
            elif self.path == "/mcp":
                self._send(200, app.mcp.handle(body, principal))
            else:
                self._send(404, {"error": "not found"})

        def _send(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format, *args):
            pass
    return Handler


def serve(app, host="0.0.0.0", port=8080):
    ThreadingHTTPServer((host, port), handler_for(app)).serve_forever()
