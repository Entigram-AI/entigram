import http.server
import json
import re
import secrets
from pathlib import Path
from entigram.federated_router import FederatedRouter


_ORIGIN_RE = re.compile(
    r"https?://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?"
)


def _validated_origin(value):
    """Return an origin that is safe for an HTTP header, or reject it."""
    if not isinstance(value, str) or not _ORIGIN_RE.fullmatch(value):
        raise ValueError("allowed origins must be exact HTTP(S) origins")
    if ":" in value.rsplit("/", 1)[-1]:
        port = int(value.rsplit(":", 1)[-1])
        if port < 1 or port > 65535:
            raise ValueError("allowed origin port must be between 1 and 65535")
    return value

class EntigramGraphQLHandler(http.server.BaseHTTPRequestHandler):
    project_dir = "."
    auth_token = None
    allowed_origins = frozenset()
    max_body_bytes = 1024 * 1024

    def _allowed_origin(self):
        supplied = self.headers.get("Origin")
        if not supplied:
            return None
        for configured in self.allowed_origins:
            if secrets.compare_digest(supplied, configured):
                return configured
        return None

    def _send_json(self, status, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        origin = self._allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(encoded)

    def _authorized(self):
        if not self.auth_token:
            return True
        supplied = self.headers.get("Authorization", "")
        return secrets.compare_digest(supplied, f"Bearer {self.auth_token}")

    def do_POST(self):
        if self.path != "/graphql":
            self._send_json(404, {"error": "not_found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return
        if content_length < 1 or content_length > self.max_body_bytes:
            self._send_json(413, {"error": "request_body_too_large"})
            return
        post_data = self.rfile.read(content_length)
        
        try:
            request_data = json.loads(post_data)
            if not isinstance(request_data, dict):
                raise ValueError("request body must be a JSON object")
            query = request_data.get("query")
            if not isinstance(query, str) or not query.strip():
                self._send_json(400, {"error": "missing_query"})
                return

            print(f"🌐 [ENTIGRAM HUB] Executing Federated Query...")
            with FederatedRouter(self.project_dir) as router:
                results = router.execute(query)
            self._send_json(200, {"data": results})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "invalid_request"})
        except Exception:
            self._send_json(500, {"error": "internal_server_error"})

    def do_OPTIONS(self):
        origin = self._allowed_origin()
        if not origin:
            self._send_json(403, {"error": "origin_not_allowed"})
            return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.end_headers()

def run_server(
    port=8080,
    project_dir=".",
    *,
    host="127.0.0.1",
    auth_token=None,
    allowed_origins=None,
    max_body_bytes=1024 * 1024,
):
    if host not in {"127.0.0.1", "localhost", "::1"} and not auth_token:
        raise ValueError("legacy GraphQL requires a bearer token when bound beyond loopback")
    handler = type(
        "ConfiguredEntigramGraphQLHandler",
        (EntigramGraphQLHandler,),
        {
            "project_dir": str(Path(project_dir).expanduser().resolve()),
            "auth_token": auth_token,
            "allowed_origins": frozenset(
                _validated_origin(origin) for origin in (allowed_origins or [])
            ),
            "max_body_bytes": max_body_bytes,
        },
    )
    server_address = (host, port)
    httpd = http.server.ThreadingHTTPServer(server_address, handler)
    print(f"🚀 Entigram Federated Hub listening on http://{host}:{port}/graphql")
    print(f"📂 Serving project: {Path(project_dir).absolute()}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server stopped.")
    finally:
        httpd.server_close()

if __name__ == "__main__":
    run_server()
