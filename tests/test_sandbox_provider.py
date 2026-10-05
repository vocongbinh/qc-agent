import shutil

import pytest

from sandbox.config import SandboxConfig
from sandbox.provider import create_provider


def test_create_provider_postgres():
    cfg = SandboxConfig(db_type="postgres")
    p = create_provider(cfg)
    assert p is not None
    assert p.supports_fast_reset is True


def test_create_provider_rejects_mongo():
    cfg = SandboxConfig(db_type="mongo")
    try:
        create_provider(cfg)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "postgres" in str(e).lower() or "unsupported" in str(e).lower()


docker = shutil.which("docker")


@pytest.mark.skipif(not docker, reason="Docker not available")
def test_postgres_provider_lifecycle():
    from sandbox.postgres import PostgresSandboxProvider

    provider = PostgresSandboxProvider(schema="catalog")
    env = provider.start()
    assert env["DB_HOST"] == "localhost"
    assert env["DB_PORT"]
    assert "DATABASE_URL" in env
    try:
        provider.reset()
    except Exception:
        pass
    provider.stop()


def test_sandbox_session_external_yields_alias_manifest(tmp_path):
    from sandbox.session import sandbox_session
    from sandbox.config import SandboxConfig

    cfg = SandboxConfig(
        sandbox_mode="external",
        schema="catalog",
        manifest_emit=True,
        manifest_aliases={"item.cafe_kem_may": "a078b105-7140-47da-bb79-7ba228808a6f"},
    )
    with sandbox_session(cfg, tmp_path) as (env, manifest):
        assert env == {}
        assert manifest["entities"]["item.cafe_kem_may"].startswith("a078b105")
