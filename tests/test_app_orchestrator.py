import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from sandbox.app_orchestrator import find_free_port, wait_for_health


def test_find_free_port_is_bindable():
    port = find_free_port()
    assert isinstance(port, int)
    assert 0 < port < 65536


def test_wait_for_health_against_local_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):  # noqa: A003
            return

    port = find_free_port()
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_for_health(f"http://127.0.0.1:{port}", "/health", timeout_s=5)
    finally:
        server.shutdown()
