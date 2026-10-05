from __future__ import annotations

from pathlib import Path
from typing import Any

from config.settings import settings
from sandbox.config import SandboxConfig, load_sandbox_config
from sandbox.session import sandbox_session


def load_run_sandbox(
    code_paths: list[str] | None,
    mode_override: str | None = None,
) -> tuple[SandboxConfig, Path, dict[str, str], dict[str, Any]]:
    """Load agent.yaml, open a sandbox session once, return env + manifest.

    Caller must keep using the yielded resources; for external mode this is cheap.
    Prefer using sandbox_session directly for long-lived db_only sessions.
    """
    cfg = load_sandbox_config(settings.agent_yaml_path)
    if mode_override:
        cfg.sandbox_mode = mode_override
    app_root = Path((code_paths or ["."])[0]).resolve()
    # For callers that only need the external-mode manifest eagerly:
    if cfg.sandbox_mode == "external":
        with sandbox_session(cfg, app_root) as (env, manifest):
            return cfg, app_root, env, manifest
    # db_only/full_local: return config+root only; caller must use sandbox_session
    return cfg, app_root, {}, {}
