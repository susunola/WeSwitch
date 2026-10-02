"""Local model router. The desktop picker changes the model id, not the provider.

Codex then sends every selected model to the current provider. Pointing that
provider at this loopback router lets the model id choose the upstream: models
this tool added go to the saved base URL with the saved key; anything else,
including the default GPT models, is forwarded to the OpenAI Responses API with
the Authorization header Codex already attached.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import urllib.error
import urllib.request

OFFICIAL_BASE = "https://api.openai.com/v1"
MAX_BODY = 2_000_000


class Router(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, routes, address=("127.0.0.1", 18766)):
        super().__init__(address, Handler)
        self.routes = routes


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length < 0 or length > MAX_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
            model = payload.get("model") if isinstance(payload, dict) else None
        except (ValueError, UnicodeDecodeError):
            model = None
        route = self.server.routes.get(model) if isinstance(model, str) else None
        upstream = (route or {}).get("base_url", OFFICIAL_BASE).rstrip("/") + self.path
        headers = {"Content-Type": "application/json"}
        if route and route.get("api_key"):
            headers["Authorization"] = "Bearer " + route["api_key"]
        else:
            incoming = self.headers.get("Authorization")
            if incoming:
                headers["Authorization"] = incoming
        request = urllib.request.Request(upstream, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120, context=ssl.create_default_context()) as response:
                data = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as error:
            data = error.read()
            status = error.code
            content_type = error.headers.get("Content-Type", "application/json")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(routes, port=18766):
    server = Router(routes, ("127.0.0.1", port))
    server.serve_forever()
    return server
