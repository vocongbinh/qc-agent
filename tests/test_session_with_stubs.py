from pathlib import Path
from unittest.mock import MagicMock

from sandbox.config import DownstreamServiceConfig, SandboxConfig
from sandbox.session import get_active_stub_provider, sandbox_session


def test_sandbox_session_with_downstream_stubs(tmp_path: Path, monkeypatch):
    stub_dir = tmp_path / "stubs" / "inventory"
    stub_dir.mkdir(parents=True)
    (stub_dir / "stock_ok.json").write_text(
        '{"request": {"method": "GET", "url": "/stock"}, "response": {"status": 200}}',
        encoding="utf-8",
    )

    cfg = SandboxConfig(
        sandbox_mode="db_only",
        db_type="postgres",
        downstream=[
            DownstreamServiceConfig(
                name="inventory",
                env_key="INVENTORY_BASE_URL",
                stub="stubs/inventory",
            )
        ],
    )

    mock_db_provider = MagicMock()
    mock_db_provider.start.return_value = {
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test_db"
    }

    monkeypatch.setattr("sandbox.session.create_provider", lambda _cfg: mock_db_provider)
    monkeypatch.setattr("sandbox.session.run_migrate", lambda *args, **kwargs: None)

    active_provider_inside = None

    with sandbox_session(cfg, tmp_path) as (env, manifest):
        active_provider_inside = get_active_stub_provider()
        assert active_provider_inside is not None
        assert "INVENTORY_BASE_URL" in env
        assert "DATABASE_URL" in env
        assert "WIREMOCK_URL" in env

    # After session exits
    assert get_active_stub_provider() is None
    mock_db_provider.stop.assert_called_once()
