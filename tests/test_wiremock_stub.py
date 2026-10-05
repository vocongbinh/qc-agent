import httpx
from pathlib import Path

from sandbox.config import DownstreamServiceConfig
from sandbox.stubs.wiremock import WireMockStubProvider


def test_wiremock_provider_lifecycle(tmp_path: Path):
    stub_dir = tmp_path / "stubs" / "inventory"
    stub_dir.mkdir(parents=True)
    (stub_dir / "stock_ok.json").write_text(
        """{
        "request": {"method": "GET", "urlPathPattern": "/api/v1/stock/.*"},
        "response": {"status": 200, "jsonBody": {"quantity": 10}}
    }""",
        encoding="utf-8",
    )
    (stub_dir / "stock_404.json").write_text(
        """{
        "request": {"method": "GET", "urlPathPattern": "/api/v1/stock/.*"},
        "response": {"status": 404, "jsonBody": {"error": "Not found"}}
    }""",
        encoding="utf-8",
    )

    downstream = [
        DownstreamServiceConfig(
            name="inventory",
            env_key="INVENTORY_BASE_URL",
            stub=str(Path("stubs") / "inventory"),
        )
    ]

    provider = WireMockStubProvider(downstream=downstream, project_root=tmp_path)
    env = provider.start()

    try:
        assert "INVENTORY_BASE_URL" in env
        assert "WIREMOCK_URL" in env
        base_url = env["INVENTORY_BASE_URL"]

        # 1. Baseline request
        with httpx.Client(base_url=base_url) as client:
            resp = client.get("/api/v1/stock/SKU-1")
            assert resp.status_code == 200
            assert resp.json()["quantity"] == 10

        # 2. Switch to 404 scenario
        provider.load_scenario("stock_404")
        with httpx.Client(base_url=base_url) as client:
            resp = client.get("/api/v1/stock/SKU-1")
            assert resp.status_code == 404
            assert resp.json()["error"] == "Not found"

        # 3. Reset back to baseline
        provider.reset()
        with httpx.Client(base_url=base_url) as client:
            resp = client.get("/api/v1/stock/SKU-1")
            assert resp.status_code == 200
            assert resp.json()["quantity"] == 10

    finally:
        provider.stop()
