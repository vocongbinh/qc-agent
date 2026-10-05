from __future__ import annotations

from typing import Protocol

from sandbox.config import SandboxConfig


class SandboxProvider(Protocol):
    supports_fast_reset: bool

    def start(self) -> dict[str, str]: ...

    def reset(self) -> None: ...

    def stop(self) -> None: ...


def create_provider(cfg: SandboxConfig) -> SandboxProvider:
    if cfg.db_type != "postgres":
        raise ValueError(
            f"Unsupported db_type for MVP: {cfg.db_type} (only postgres)"
        )
    from sandbox.postgres import PostgresSandboxProvider

    return PostgresSandboxProvider(schema=cfg.schema)
