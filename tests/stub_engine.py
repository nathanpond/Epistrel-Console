"""A stdlib HTTP server that answers /health like the Engine (and ``GET /stories`` with an empty
list, for the status page's service-key check), for live tests."""

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HEALTHY = {"status": "ok", "checks": {"database": "ok", "ffmpeg": "missing"}}
STORIES: dict[str, list[object]] = {"items": []}


@contextmanager
def stub_engine(delay_s: float = 0.0) -> Iterator[ThreadingHTTPServer]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if delay_s:
                time.sleep(delay_s)
            body = STORIES if self.path.split("?", 1)[0] == "/stories" else HEALTHY
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def base_url(server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"
