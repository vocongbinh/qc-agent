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
