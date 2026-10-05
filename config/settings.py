from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: str = "auto"  # "auto", "antigravity", "openai"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    default_model: str = "gpt-4o"
    planner_model: str = "gpt-4o"
    generator_model: str = "gpt-4o"
    reporter_model: str = "gpt-4o-mini"
    vision_model: str = "gpt-4o"
    antigravity_model: str = "gemini-2.5-flash"
    antigravity_planner_model: str = "gemini-2.5-flash"
    antigravity_generator_model: str = "gemini-2.5-flash"
    antigravity_reporter_model: str = "gemini-2.5-flash"
    antigravity_vision_model: str = "gemini-2.5-flash"
    # Paths
    project_root: Path = Path(__file__).parent.parent
    test_cases_dir: Path = project_root / "test_cases"
    reports_dir: Path = project_root / "reports"
    prompts_dir: Path = project_root / "prompts"

    # Behavior
    enable_human_review: bool = True
    max_retries: int = 2
    temperature: float = 0.2

    # Code Intelligence
    enable_codeintel: bool = False
    codeintel_db_path: Path = project_root / ".codeintel_db"

    # Sandbox / ID policy
    sandbox_mode: str = "external"  # external | db_only | full_local
    agent_yaml_path: Path = project_root / "agent.yaml"

    # ----- Stack under test (chỉnh theo môi trường thật) -----
    # App / API
    default_base_url: str = "http://localhost:8000"
    # Host:port mà Toxiproxy proxy TỚI (upstream). 
    # - App chạy trên máy host: host.docker.internal:8000 (Docker Desktop)
    # - Linux thuần: 172.17.0.1:8000 hoặc IP máy
    # - App trong cùng compose network: app:8000
    app_upstream_host: str = "host.docker.internal"
    app_upstream_port: int = 8000

    # Toxiproxy
    toxiproxy_api_url: str = "http://localhost:8474"
    toxiproxy_proxy_url: str = "http://localhost:18080"
    toxiproxy_listen: str = "0.0.0.0:18080"
    toxiproxy_proxy_name: str = "api_proxy"

    # Docker container names (khớp docker-compose.chaos.yml)
    redis_container: str = "redis"
    db_container: str = "db"
    kafka_container: str = "kafka"

    # Kafka
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_probe: str = "qc-chaos-probe"

    @property
    def app_upstream(self) -> str:
        """host:port cho Toxiproxy upstream."""
        return f"{self.app_upstream_host}:{self.app_upstream_port}"


settings = Settings()
