from __future__ import annotations

import socket
import subprocess
import time
import uuid
from pathlib import Path

import psycopg

_COMPOSE_FILE = (
    Path(__file__).resolve().parent.parent
    / "environments"
    / "docker-compose.sandbox-pg.yml"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class PostgresSandboxProvider:
    supports_fast_reset = True

    def __init__(self, schema: str = "catalog", compose_file: Path | None = None):
        self.schema = schema
        self.compose_file = Path(compose_file or _COMPOSE_FILE)
        self.project_name = f"qc_sandbox_{uuid.uuid4().hex[:8]}"
        self.port: int | None = None
        self._env: dict[str, str] = {}
        self._template_name: str | None = None

    def _compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if self.port is None:
            raise RuntimeError("port not allocated; call start() first")
        cmd = [
            "docker",
            "compose",
            "-p",
            self.project_name,
            "-f",
            str(self.compose_file),
            *args,
        ]
        env = {**dict(**__import__("os").environ), "SANDBOX_PG_PORT": str(self.port)}
        return subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
            env=env,
        )

    def start(self) -> dict[str, str]:
        if not self.compose_file.exists():
            raise FileNotFoundError(f"Compose file missing: {self.compose_file}")
        self.port = _free_port()
        self._compose("up", "-d")
        deadline = time.time() + 60
        last_err = ""
        while time.time() < deadline:
            try:
                with psycopg.connect(
                    host="localhost",
                    port=self.port,
                    user="test",
                    password="test",
                    dbname="catalog_service",
                    connect_timeout=2,
                ) as conn:
                    conn.execute("SELECT 1")
                break
            except Exception as exc:  # noqa: BLE001 — poll until ready
                last_err = str(exc)
                time.sleep(1)
        else:
            self.stop()
            raise TimeoutError(f"Postgres not ready: {last_err}")

        self._env = {
            "DB_HOST": "localhost",
            "DB_PORT": str(self.port),
            "DB_USER": "test",
            "DB_PASSWORD": "test",
            "DB_NAME": "catalog_service",
            "DB_SCHEMA": self.schema,
            "DATABASE_URL": (
                f"postgresql://test:test@localhost:{self.port}/catalog_service"
            ),
        }
        return dict(self._env)

    def reset(self) -> None:
        if not self._env:
            raise RuntimeError("Provider not started")
        url = self._env["DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as conn:
            rows = conn.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = %s
                """,
                (self.schema,),
            ).fetchall()
            if not rows:
                return
            tables = ", ".join(f'"{self.schema}"."{r[0]}"' for r in rows)
            conn.execute(f"TRUNCATE {tables} CASCADE")


    def bake_template(self, template_name: str = "app_seed") -> None:
        """Snapshot current DB as a TEMPLATE database for fast warm resets."""
        if not self._env:
            raise RuntimeError("Provider not started")
        admin_url = (
            f"postgresql://test:test@localhost:{self.port}/postgres"
        )
        db_name = self._env["DB_NAME"]
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (db_name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{template_name}"')
            conn.execute(
                f'CREATE DATABASE "{template_name}" TEMPLATE "{db_name}"'
            )
        self._template_name = template_name

    def reset_via_template(self, template_name: str | None = None) -> None:
        """Recreate working DB FROM TEMPLATE (warm path). Falls back to TRUNCATE."""
        tmpl = template_name or getattr(self, "_template_name", None)
        if not tmpl:
            self.reset()
            return
        if not self._env:
            raise RuntimeError("Provider not started")
        admin_url = f"postgresql://test:test@localhost:{self.port}/postgres"
        db_name = self._env["DB_NAME"]
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (db_name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            conn.execute(f'CREATE DATABASE "{db_name}" TEMPLATE "{tmpl}"')

    def stop(self) -> None:
        if self.port is None:
            return
        try:
            self._compose("down", "-v", check=False)
        finally:
            self.port = None
            self._env = {}
