from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DownstreamServiceConfig:
    name: str
    env_key: str
    stub: str = ""


@dataclass
class SandboxConfig:
    sandbox_mode: str = "external"
    db_type: str = "postgres"
    schema: str = "catalog"
    migrate_cmd: str = ""
    seed_cmd: str = ""
    seed_strategy: str = "sql"
    seed_paths: list[str] = field(default_factory=list)
    manifest_emit: bool = True
    manifest_aliases: dict[str, str] = field(default_factory=dict)
    manifest_path: str = ""
    health_path: str = "/health"
    app_start_cmd: str = ""
    app_cwd: str = ""
    downstream: list[DownstreamServiceConfig] = field(default_factory=list)
    forbid_fabricated_ids: bool = True
    require_setup_or_seed_for_mutate: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


def load_sandbox_config(path: Path | str) -> SandboxConfig:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    # Support nested sections or top-level fallback
    service = data.get("service") or {}
    sandbox_section = data.get("sandbox") or {}
    database = data.get("database") or {}
    planner_section = data.get("planner") or {}
    seed = data.get("seed") or database.get("seed") or {}
    manifest = data.get("manifest") or {}

    # Downstream services parsing
    downstream_raw = data.get("downstream") or []
    downstream: list[DownstreamServiceConfig] = []
    for d in downstream_raw:
        if isinstance(d, dict):
            downstream.append(
                DownstreamServiceConfig(
                    name=str(d.get("name", "")),
                    env_key=str(d.get("env_key", "")),
                    stub=str(d.get("stub", "")),
                )
            )

    sandbox_mode = str(
        sandbox_section.get("mode") or data.get("sandbox_mode") or "external"
    )
    db_type = str(
        database.get("type") or data.get("db_type") or "postgres"
    )
    schema = str(
        database.get("schema") or data.get("schema") or "public"
    )
    migrate_cmd = str(
        database.get("migrate_cmd") or data.get("migrate_cmd") or ""
    )
    seed_cmd = str(
        database.get("seed_cmd") or data.get("seed_cmd") or ""
    )
    health_path = str(
        service.get("health_path") or data.get("health_path") or "/health"
    )
    app_start_cmd = str(
        service.get("start_cmd") or data.get("app_start_cmd") or ""
    )
    app_cwd = str(
        service.get("cwd") or data.get("app_cwd") or ""
    )

    return SandboxConfig(
        sandbox_mode=sandbox_mode,
        db_type=db_type,
        schema=schema,
        migrate_cmd=migrate_cmd,
        seed_cmd=seed_cmd,
        seed_strategy=str(seed.get("strategy", "sql")),
        seed_paths=list(seed.get("paths") or []),
        manifest_emit=bool(manifest.get("emit", True)),
        manifest_aliases=dict(manifest.get("aliases") or {}),
        manifest_path=str(manifest.get("path") or ""),
        health_path=health_path,
        app_start_cmd=app_start_cmd,
        app_cwd=app_cwd,
        downstream=downstream,
        forbid_fabricated_ids=bool(
            planner_section.get("forbid_fabricated_ids", True)
        ),
        require_setup_or_seed_for_mutate=bool(
            planner_section.get("require_setup_or_seed_for_mutate", True)
        ),
        raw=data,
    )

