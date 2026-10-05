from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8000"))
INVENTORY_BASE_URL = os.environ.get("INVENTORY_BASE_URL", "http://127.0.0.1:8080").rstrip("/")


class OrderServiceHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        pass  # Quiet logging

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "UP"}')
            return

        if self.path.startswith("/api/v1/orders/"):
            order_id = self.path.split("/")[-1]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"order_id": order_id, "status": "CONFIRMED"}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/api/v1/orders":
            length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(body_bytes)
            sku = payload.get("sku", "")

            # Call downstream inventory service
            inv_url = f"{INVENTORY_BASE_URL}/api/v1/stock/{sku}"
            try:
                req = urllib.request.Request(inv_url, headers={"User-Agent": "OrderService/1.0"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status == 200:
                        # Order confirmed
                        self.send_response(201)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({
                                "order_id": "ord_888",
                                "sku": sku,
                                "status": "CONFIRMED",
                            }).encode("utf-8")
                        )
                        return
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "Item out of stock or not found"}')
                    return
                elif exc.code in (504, 502, 503):
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "Inventory service timeout or error"}')
                    return
                else:
                    self.send_response(500)
                    self.end_headers()
                    return
            except Exception:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Failed to connect to inventory"}')
                return

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), OrderServiceHandler)
    print(f"OrderService running on port {PORT}, INVENTORY={INVENTORY_BASE_URL}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
