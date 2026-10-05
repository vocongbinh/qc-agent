from __future__ import annotations

import os
import socket
import subprocess
import time
from contextlib import contextmanager
from typing import Iterator

import httpx


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_health(base_url: str, path: str = "/health", timeout_s: float = 60) -> None:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    deadline = time.time() + timeout_s
    last = ""
    with httpx.Client(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                resp = client.get(url)
                if resp.status_code < 500:
                    return
                last = f"status {resp.status_code}"
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
            time.sleep(0.5)
    raise TimeoutError(f"App health check failed for {url}: {last}")


@contextmanager
def AppProcess(
    start_cmd: str,
    cwd: str,
    db_env: dict[str, str],
    health_path: str = "/health",
    port: int | None = None,
) -> Iterator[str]:
    """Start app subprocess, yield base URL, terminate on exit."""
    if not start_cmd:
        raise ValueError("app_start_cmd is required for full_local mode")
    listen_port = port or find_free_port()
    env = {**os.environ, **db_env, "PORT": str(listen_port)}
    proc = subprocess.Popen(
        start_cmd,
        shell=True,
        cwd=cwd or None,
        env=env,
    )
    base_url = f"http://127.0.0.1:{listen_port}"
    try:
        wait_for_health(base_url, health_path, timeout_s=90)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
