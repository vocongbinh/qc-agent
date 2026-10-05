from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SandboxConfig:
    sandbox_mode: str = "external"
    db_type: str = "postgres"
    schema: str = "catalog"
    migrate_cmd: str = ""
    seed_strategy: str = "sql"
    seed_paths: list[str] = field(default_factory=list)
    manifest_emit: bool = True
    manifest_aliases: dict[str, str] = field(default_factory=dict)
    health_path: str = "/health"
    app_start_cmd: str = ""
    app_cwd: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def load_sandbox_config(path: Path | str) -> SandboxConfig:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    seed = data.get("seed") or {}
    manifest = data.get("manifest") or {}
    return SandboxConfig(
        sandbox_mode=str(data.get("sandbox_mode", "external")),
        db_type=str(data.get("db_type", "postgres")),
        schema=str(data.get("schema", "public")),
        migrate_cmd=str(data.get("migrate_cmd", "")),
        seed_strategy=str(seed.get("strategy", "sql")),
        seed_paths=list(seed.get("paths") or []),
        manifest_emit=bool(manifest.get("emit", True)),
        manifest_aliases=dict(manifest.get("aliases") or {}),
        health_path=str(data.get("health_path", "/health")),
        app_start_cmd=str(data.get("app_start_cmd", "")),
        app_cwd=str(data.get("app_cwd", "")),
        raw=data,
    )
