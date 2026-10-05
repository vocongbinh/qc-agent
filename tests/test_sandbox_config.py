from pathlib import Path
from agents.state import AgentState
from config.settings import settings


def test_agent_state_has_seed_manifest_annotation():
    assert "seed_manifest" in AgentState.__annotations__


def test_sandbox_settings_defaults():
    assert hasattr(settings, "sandbox_mode")
    assert settings.sandbox_mode == "external"
    assert hasattr(settings, "agent_yaml_path")
    assert isinstance(settings.agent_yaml_path, Path)


from sandbox.config import load_sandbox_config, SandboxConfig


def test_load_agent_yaml(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text(
        """
sandbox_mode: external
db_type: postgres
schema: catalog
migrate_cmd: "npm run drizzle:migrate"
seed:
  strategy: sql
  paths: ["seed/*.sql"]
manifest:
  emit: true
  aliases:
    item.cafe_kem_may: "a078b105-7140-47da-bb79-7ba228808a6f"
health_path: /health
""",
        encoding="utf-8",
    )
    cfg = load_sandbox_config(p)
    assert isinstance(cfg, SandboxConfig)
    assert cfg.sandbox_mode == "external"
    assert cfg.db_type == "postgres"
    assert cfg.manifest_aliases["item.cafe_kem_may"].startswith("a078b105")
