from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sandbox.config import SandboxConfig
from sandbox.provider import create_provider
from sandbox.seed import (
    apply_sql_seeds,
    build_manifest_from_aliases,
    hash_seed_files,
    run_migrate,
)


@contextmanager
def sandbox_session(
    cfg: SandboxConfig,
    app_project_root: Path,
) -> Iterator[tuple[dict[str, str], dict]]:
    """Yields (db_env, seed_manifest).

    external mode: empty env + alias manifest (no Docker).
    db_only / full_local: start Postgres, migrate, seed, then stop on exit.
    """
    if cfg.sandbox_mode == "external":
        version = (
            hash_seed_files(app_project_root, cfg.seed_paths)
            if cfg.seed_paths and app_project_root.exists()
            else "external"
        )
        # Prefer hashing aliases when seed files are not under app_project_root
        if version == "external" or not any(app_project_root.glob(p) for p in (cfg.seed_paths or [])):
            version = "external"
        manifest = (
            build_manifest_from_aliases(cfg, version_suffix=version)
            if cfg.manifest_emit
            else {"version": "external", "entities": {}}
        )
        yield {}, manifest
        return

    provider = create_provider(cfg)
    env: dict[str, str] = {}
    try:
        env = provider.start()
        run_migrate(cfg.migrate_cmd, cwd=app_project_root, env=env)
        if cfg.seed_strategy == "sql" and cfg.seed_paths:
            apply_sql_seeds(env["DATABASE_URL"], app_project_root, cfg.seed_paths)
        version = hash_seed_files(app_project_root, cfg.seed_paths) if cfg.seed_paths else "session"
        manifest = build_manifest_from_aliases(cfg, version_suffix=version)
        yield env, manifest
    finally:
        provider.stop()
