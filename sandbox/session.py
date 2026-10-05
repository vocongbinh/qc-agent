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
    run_seed_cmd,
)
from sandbox.stubs.base import StubProvider
from sandbox.stubs.wiremock import WireMockStubProvider

_ACTIVE_STUB_PROVIDER: StubProvider | None = None


def get_active_stub_provider() -> StubProvider | None:
    """Return the currently active StubProvider for the running sandbox session."""
    return _ACTIVE_STUB_PROVIDER


@contextmanager
def sandbox_session(
    cfg: SandboxConfig,
    app_project_root: Path,
) -> Iterator[tuple[dict[str, str], dict]]:
    """Yields (db_env, seed_manifest).

    external mode: empty env + alias manifest (no Docker).
    db_only / full_local: start Postgres + WireMock stubs, migrate, seed, then stop on exit.
    """
    global _ACTIVE_STUB_PROVIDER

    if cfg.sandbox_mode == "external":
        version = (
            hash_seed_files(app_project_root, cfg.seed_paths)
            if cfg.seed_paths and app_project_root.exists()
            else "external"
        )
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
    stub_provider: StubProvider | None = None
    if cfg.downstream:
        stub_provider = WireMockStubProvider(
            downstream=cfg.downstream,
            project_root=app_project_root,
        )
    _ACTIVE_STUB_PROVIDER = stub_provider

    env: dict[str, str] = {}
    try:
        env = provider.start()
        if stub_provider:
            stub_env = stub_provider.start()
            env.update(stub_env)

        run_migrate(cfg.migrate_cmd, cwd=app_project_root, env=env)

        # Dynamic seed command or SQL seed files
        seed_cmd_entities: dict = {}
        if cfg.seed_cmd:
            seed_data = run_seed_cmd(
                cfg.seed_cmd,
                cwd=app_project_root,
                env=env,
                manifest_path=cfg.manifest_path,
            )
            seed_cmd_entities = seed_data.get("entities") or {}

        if cfg.seed_strategy == "sql" and cfg.seed_paths:
            apply_sql_seeds(env["DATABASE_URL"], app_project_root, cfg.seed_paths)

        version = hash_seed_files(app_project_root, cfg.seed_paths) if cfg.seed_paths else "session"
        manifest = build_manifest_from_aliases(cfg, version_suffix=version)
        if seed_cmd_entities:
            manifest.setdefault("entities", {}).update(seed_cmd_entities)

        if cfg.sandbox_mode == "full_local":
            from sandbox.app_orchestrator import AppProcess

            with AppProcess(
                start_cmd=cfg.app_start_cmd,
                cwd=cfg.app_cwd or str(app_project_root),
                db_env=env,
                health_path=cfg.health_path,
            ) as base_url:
                env = {**env, "BASE_URL": base_url, "DEFAULT_BASE_URL": base_url}
                yield env, manifest
        else:
            yield env, manifest
    finally:
        _ACTIVE_STUB_PROVIDER = None
        if stub_provider:
            try:
                stub_provider.stop()
            except Exception:
                pass
        provider.stop()

