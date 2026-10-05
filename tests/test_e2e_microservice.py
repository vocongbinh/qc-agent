from pathlib import Path

from agents.api_executor import _run_single_api_test
from sandbox.config import load_sandbox_config
from sandbox.session import sandbox_session


def test_e2e_microservice_sandbox_with_stubs():
    order_svc_root = Path(__file__).parent.parent / "examples" / "order-service"
    cfg_file = order_svc_root / "agent.yaml"
    cfg = load_sandbox_config(cfg_file)

    with sandbox_session(cfg, order_svc_root) as (env, manifest):
        sut_url = env["BASE_URL"]

        # 1. Normal order creation with seed SKU
        tc1 = {
            "id": "TC_1",
            "test_data": {"sku": "{{SEEDED_PRODUCT_SKU}}"},
            "steps": [
                {
                    "step": 1,
                    "action": "POST /api/v1/orders",
                    "data": {"sku": "{{sku}}", "qty": 1},
                    "extract": {"order_id": "order_id"},
                }
            ],
            "expected": {"status_code": 201},
        }
        res1 = _run_single_api_test(tc1, base_url=sut_url, seed_manifest=manifest)
        assert res1["status"] == "passed"
        order_id = res1["extracted"].get("order_id")
        assert order_id == "ord_888"

        # 2. Downstream 404 scenario
        tc2 = {
            "id": "TC_2",
            "stub_scenario": "stock_404",
            "test_data": {"sku": "SKU-999"},
            "steps": [{"step": 1, "action": "POST /api/v1/orders", "data": {"sku": "{{sku}}"}}],
            "expected": {"status_code": 400},
        }
        res2 = _run_single_api_test(tc2, base_url=sut_url, seed_manifest=manifest)
        assert res2["status"] == "passed"

        # 3. Downstream timeout scenario
        tc3 = {
            "id": "TC_3",
            "stub_scenario": "stock_timeout",
            "test_data": {"sku": "SKU-1"},
            "steps": [{"step": 1, "action": "POST /api/v1/orders", "data": {"sku": "{{sku}}"}}],
            "expected": {"status_code": 502},
        }
        res3 = _run_single_api_test(tc3, base_url=sut_url, seed_manifest=manifest)
        assert res3["status"] == "passed"

        # 4. Query extracted order
        tc4 = {
            "id": "TC_4",
            "test_data": {"order_id": order_id},
            "steps": [{"step": 1, "action": "GET /api/v1/orders/{{order_id}}"}],
            "expected": {"status_code": 200},
        }
        res4 = _run_single_api_test(tc4, base_url=sut_url, seed_manifest=manifest)
        assert res4["status"] == "passed"
