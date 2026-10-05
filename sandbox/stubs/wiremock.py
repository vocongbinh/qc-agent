from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx

from sandbox.config import DownstreamServiceConfig

logger = logging.getLogger(__name__)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _LocalWiremockHandler(BaseHTTPRequestHandler):
    """In-process mock server emulating WireMock Admin API + request matching.

    Ensures tests and developer laptops without running Docker daemons
    can execute stubs and scenarios with full reliability.
    """

    server: _LocalWiremockServer

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet logging

    def do_GET(self) -> None:
        if self.path == "/__admin/mappings":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"mappings": self.server.mappings}).encode("utf-8"))
            return

        self._match_and_respond("GET")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        data = json.loads(body) if body else {}

        if self.path == "/__admin/mappings":
            self.server.mappings.append(data)
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "Created", "mapping": data}).encode("utf-8"))
            return

        if self.path in ("/__admin/mappings/reset", "/__admin/reset"):
            self.server.reset_to_baseline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "Reset"}')
            return

        if self.path == "/__admin/scenarios/reset":
            self.server.reset_to_baseline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "Scenarios Reset"}')
            return

        self._match_and_respond("POST", data)

    def do_PUT(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        data = json.loads(body) if body else {}
        self._match_and_respond("PUT", data)

    def do_DELETE(self) -> None:
        if self.path == "/__admin/mappings":
            self.server.mappings.clear()
            self.send_response(200)
            self.end_headers()
            return
        self._match_and_respond("DELETE")

    def _match_and_respond(self, method: str, req_body: Any = None) -> None:
        path = self.path
        # Search in reverse order so latest scenario overrides have precedence
        for mapping in reversed(self.server.mappings):
            req_spec = mapping.get("request", {})
            req_method = (req_spec.get("method") or "GET").upper()
            if req_method != method.upper() and req_method != "ANY":
                continue

            matched = False
            if "url" in req_spec and req_spec["url"] == path:
                matched = True
            elif "urlPath" in req_spec and req_spec["urlPath"] == path.split("?")[0]:
                matched = True
            elif "urlPathPattern" in req_spec:
                pat = req_spec["urlPathPattern"]
                if re.match(pat, path.split("?")[0]):
                    matched = True
            elif "urlPattern" in req_spec:
                pat = req_spec["urlPattern"]
                if re.match(pat, path):
                    matched = True
            elif not any(k in req_spec for k in ("url", "urlPath", "urlPathPattern", "urlPattern")):
                matched = True

            if matched:
                resp_spec = mapping.get("response", {})
                status = int(resp_spec.get("status", 200))
                headers = resp_spec.get("headers", {"Content-Type": "application/json"})
                fixed_delay = resp_spec.get("fixedDelayMilliseconds")
                if fixed_delay:
                    time.sleep(float(fixed_delay) / 1000.0)

                resp_body: bytes = b""
                if "jsonBody" in resp_spec:
                    resp_body = json.dumps(resp_spec["jsonBody"]).encode("utf-8")
                elif "body" in resp_spec:
                    resp_body = str(resp_spec["body"]).encode("utf-8")

                self.send_response(status)
                for h_name, h_val in headers.items():
                    self.send_header(h_name, str(h_val))
                self.end_headers()
                self.wfile.write(resp_body)
                return

        # No match found
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "No matching WireMock stub"}')


class _LocalWiremockServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_cls: type[BaseHTTPRequestHandler]):
        super().__init__(server_address, handler_cls)
        self.mappings: list[dict[str, Any]] = []
        self.baseline_mappings: list[dict[str, Any]] = []

    def set_baseline(self, mappings: list[dict[str, Any]]) -> None:
        self.baseline_mappings = [dict(m) for m in mappings]
        self.reset_to_baseline()

    def reset_to_baseline(self) -> None:
        self.mappings = [dict(m) for m in self.baseline_mappings]


class WireMockStubProvider:
    """Manages WireMock lifecycle, downstream service routing, and scenario switching."""

    def __init__(
        self,
        downstream: list[DownstreamServiceConfig],
        project_root: Path | None = None,
        port: int | None = None,
        prefer_docker: bool = False,
    ):
        self.downstream = downstream
        self.project_root = Path(project_root or ".").resolve()
        self.port = port
        self.prefer_docker = prefer_docker
        self.docker_container: str | None = None
        self._local_server: _LocalWiremockServer | None = None
        self._server_thread: threading.Thread | None = None
        self._catalog: dict[str, list[dict[str, Any]]] = {}
        self._baseline_mappings: list[dict[str, Any]] = []

    def _discover_mappings(self) -> None:
        """Scan stub directories defined in downstream configs."""
        self._catalog = {}
        self._baseline_mappings = []

        for ds in self.downstream:
            stub_dir = self.project_root / ds.stub
            if not stub_dir.exists():
                continue

            for file in sorted(stub_dir.glob("*.json")):
                scenario_name = file.stem  # e.g. 'stock_ok', 'stock_404'
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                    mappings = data.get("mappings") if isinstance(data, dict) and "mappings" in data else [data]
                    self._catalog.setdefault(scenario_name, []).extend(mappings)
                    # Also register with service prefix, e.g. 'inventory_ok', 'inventory/stock_ok'
                    prefix_name = f"{ds.name}_{scenario_name}"
                    self._catalog.setdefault(prefix_name, []).extend(mappings)
                    self._catalog.setdefault(f"{ds.name}/{scenario_name}", []).extend(mappings)

                    # Baseline heuristic: files containing 'ok', 'default', or 'success'
                    if any(w in scenario_name.lower() for w in ("ok", "default", "success")):
                        self._baseline_mappings.extend(mappings)
                except Exception as exc:
                    logger.warning("Failed to parse stub file %s: %s", file, exc)

        if not self._baseline_mappings and self._catalog:
            # Fallback: load first mapping in each service as baseline
            for mappings in self._catalog.values():
                self._baseline_mappings.extend(mappings)
                break

    def _try_start_docker(self) -> bool:
        if not self.prefer_docker:
            return False
        container_name = f"wiremock_{uuid.uuid4().hex[:8]}"
        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-p",
            f"{self.port}:8080",
            "wiremock/wiremock:3.9.1",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                self.docker_container = container_name
                # Wait for WireMock ready
                deadline = time.time() + 30
                while time.time() < deadline:
                    try:
                        resp = httpx.get(f"http://127.0.0.1:{self.port}/__admin/mappings", timeout=1.0)
                        if resp.status_code == 200:
                            return True
                    except Exception:
                        time.sleep(0.5)
                # Timeout
                self._stop_docker()
        except Exception:
            pass
        return False

    def _stop_docker(self) -> None:
        if self.docker_container:
            try:
                subprocess.run(["docker", "stop", self.docker_container], capture_output=True, check=False)
            except Exception:
                pass
            self.docker_container = None

    def _start_local_mock(self) -> None:
        self._local_server = _LocalWiremockServer(("127.0.0.1", self.port), _LocalWiremockHandler)
        self._local_server.set_baseline(self._baseline_mappings)
        self._server_thread = threading.Thread(target=self._local_server.serve_forever, daemon=True)
        self._server_thread.start()

    def start(self) -> dict[str, str]:
        if self.port is None:
            self.port = find_free_port()

        self._discover_mappings()

        started = self._try_start_docker()
        if not started:
            self._start_local_mock()

        # Load baseline mappings if in Docker
        if self.docker_container:
            self.reset()

        base_url = f"http://127.0.0.1:{self.port}"
        env: dict[str, str] = {
            "WIREMOCK_URL": base_url,
        }
        for ds in self.downstream:
            if ds.env_key:
                env[ds.env_key] = base_url

        return env

    def load_scenario(self, scenario_name: str) -> None:
        """Switch active mappings to scenario mappings."""
        target_mappings = self._catalog.get(scenario_name)
        if not target_mappings:
            # Check case-insensitive
            for k, v in self._catalog.items():
                if k.lower() == scenario_name.lower():
                    target_mappings = v
                    break

        if not target_mappings:
            logger.warning("Scenario '%s' not found in catalog %s", scenario_name, list(self._catalog.keys()))
            return

        if self._local_server:
            # Append/override mappings in local server
            self._local_server.mappings.extend(target_mappings)
            return

        # If Docker WireMock
        base_url = f"http://127.0.0.1:{self.port}"
        try:
            with httpx.Client(base_url=base_url, timeout=3.0) as client:
                for m in target_mappings:
                    client.post("/__admin/mappings", json=m)
        except Exception as exc:
            logger.warning("Failed to post scenario mappings to WireMock: %s", exc)

    def reset(self) -> None:
        """Reset mappings back to baseline."""
        if self._local_server:
            self._local_server.reset_to_baseline()
            return

        if self.docker_container:
            base_url = f"http://127.0.0.1:{self.port}"
            try:
                with httpx.Client(base_url=base_url, timeout=3.0) as client:
                    client.post("/__admin/mappings/reset")
                    for m in self._baseline_mappings:
                        client.post("/__admin/mappings", json=m)
            except Exception as exc:
                logger.warning("Failed to reset WireMock mappings: %s", exc)

    def stop(self) -> None:
        if self._local_server:
            self._local_server.shutdown()
            self._local_server.server_close()
            self._local_server = None
        self._stop_docker()
        self.port = None
