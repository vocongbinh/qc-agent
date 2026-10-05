from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

import psycopg

from sandbox.config import SandboxConfig


def build_manifest_from_aliases(
    cfg: SandboxConfig, version_suffix: str = ""
) -> dict[str, Any]:
    suffix = version_suffix or "static"
    return {
        "version": f"{cfg.schema}@{suffix}",
        "entities": dict(cfg.manifest_aliases),
    }


def apply_sql_seeds(
    database_url: str, project_root: Path, glob_patterns: list[str]
) -> None:
    files: list[Path] = []
    for pattern in glob_patterns:
        files.extend(sorted(project_root.glob(pattern)))
    if not files:
        raise FileNotFoundError(
            f"No seed files for {glob_patterns} under {project_root}"
        )
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS catalog")
        for f in files:
            sql = f.read_text(encoding="utf-8")
            if sql.strip():
                conn.execute(sql)


def run_migrate(cmd: str, cwd: Path, env: dict[str, str]) -> None:
    if not cmd:
        return
    merged = {**os.environ, **env}
    subprocess.run(cmd, shell=True, cwd=str(cwd), env=merged, check=True)


def hash_seed_files(project_root: Path, patterns: list[str]) -> str:
    h = hashlib.sha256()
    for pattern in patterns:
        for f in sorted(project_root.glob(pattern)):
            h.update(f.read_bytes())
    return h.hexdigest()[:12]
