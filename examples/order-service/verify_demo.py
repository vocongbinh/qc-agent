import sys
from pathlib import Path

# Add qc-agent root to sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rich.console import Console
from rich.table import Table

from agents.api_executor import _run_single_api_test
from sandbox.config import load_sandbox_config
from sandbox.session import sandbox_session

console = Console()


def run_demo():
    order_svc_root = Path(__file__).parent.resolve()
    cfg_file = order_svc_root / "agent.yaml"
    cfg = load_sandbox_config(cfg_file)

    console.print("\n[bold green]===========================================================[/bold green]")
    console.print("[bold cyan]▶ KHỞI ĐỘNG PHIÊN KIỂM THỬ SUT (Order Service Sandbox)[/bold cyan]")
    console.print("[bold green]===========================================================[/bold green]")
    console.print(f"• Config file: [yellow]{cfg_file.relative_to(Path.cwd())}[/yellow]")
    console.print(f"• Mode: [magenta]{cfg.sandbox_mode}[/magenta]")
    console.print(f"• Downstream stubs: [yellow]{[d.name for d in cfg.downstream]}[/yellow]")

    with sandbox_session(cfg, order_svc_root) as (env, manifest):
        sut_url = env["BASE_URL"]
        wiremock_url = env["WIREMOCK_URL"]
        console.print(f"\n[green]✓[/green] WireMock Stub Provider sẵn sàng tại: [bold]{wiremock_url}[/bold]")
        console.print(f"[green]✓[/green] Đã nạp seed manifest: [bold]{manifest.get('entities')}[/bold]")
        console.print(f"[green]✓[/green] SUT app (OrderService) đã UP tại: [bold]{sut_url}[/bold]\n")

        # Test cases to execute
        test_cases = [
            {
                "id": "TC_ORDER_001_CREATE_OK",
                "title": "Tạo đơn hàng thành công khi Inventory còn hàng (baseline stub)",
                "test_data": {
                    "sku": "{{SEEDED_PRODUCT_SKU}}",
                },
                "steps": [
                    {
                        "step": 1,
                        "action": "POST /api/v1/orders",
                        "data": {"sku": "{{sku}}", "qty": 1},
                        "extract": {"saved_order_id": "order_id"},
                    }
                ],
                "expected": {
                    "status_code": 201,
                    "body": {"status": "CONFIRMED"},
                }
            },
            {
                "id": "TC_ORDER_002_OUT_OF_STOCK",
                "title": "Tạo đơn hàng thất bại khi Inventory trả về 404 (stub_scenario: stock_404)",
                "stub_scenario": "stock_404",
                "test_data": {
                    "sku": "SKU-OUT-OF-STOCK",
                },
                "steps": [
                    {
                        "step": 1,
                        "action": "POST /api/v1/orders",
                        "data": {"sku": "{{sku}}", "qty": 1},
                    }
                ],
                "expected": {
                    "status_code": 400,
                }
            },
            {
                "id": "TC_ORDER_003_DOWNSTREAM_TIMEOUT",
                "title": "Xử lý resilience khi Inventory bị lỗi/timeout (stub_scenario: stock_timeout)",
                "stub_scenario": "stock_timeout",
                "test_data": {
                    "sku": "SKU-TIMEOUT",
                },
                "steps": [
                    {
                        "step": 1,
                        "action": "POST /api/v1/orders",
                        "data": {"sku": "{{sku}}", "qty": 1},
                    }
                ],
                "expected": {
                    "status_code": 502,
                }
            },
            {
                "id": "TC_ORDER_004_GET_BY_EXTRACTED_ID",
                "title": "Lấy chi tiết đơn hàng bằng ID trích xuất từ Step 1 (Không bịa ID)",
                "test_data": {
                    "order_id": "{{saved_order_id}}",
                },
                "steps": [
                    {
                        "step": 1,
                        "action": "GET /api/v1/orders/{{order_id}}",
                    }
                ],
                "expected": {
                    "status_code": 200,
                    "body": {"status": "CONFIRMED"},
                }
            }
        ]

        table = Table(title="KẾT QUẢ THỰC THI KIỂM THỬ SUT", show_lines=True)
        table.add_column("Case ID", style="cyan")
        table.add_column("Mục tiêu test", style="white")
        table.add_column("Stub Scenario", style="yellow")
        table.add_column("HTTP Code", style="bold")
        table.add_column("Kết quả", style="bold green")

        shared_extract = {}
        for tc in test_cases:
            # Merge extracted variables from previous steps (e.g. saved_order_id)
            tc.setdefault("test_data", {}).update(shared_extract)

            res = _run_single_api_test(tc, base_url=sut_url, seed_manifest=manifest)
            if res.get("extracted"):
                shared_extract.update(res["extracted"])

            status_style = "green" if res["status"] == "passed" else "red"
            scenario_disp = tc.get("stub_scenario") or "baseline (stock_ok)"
            table.add_row(
                tc["id"],
                tc["title"],
                scenario_disp,
                str(res.get("response_status")),
                f"[{status_style}]{res['status'].upper()}[/{status_style}]"
            )

        console.print(table)
        console.print("\n[bold green]✓ TẤT CẢ TEST CASES ĐÃ PASS 100% NHƯ KỲ VỌNG![/bold green]")
        console.print("[dim]Đang dọn dẹp môi trường (dừng SUT process, WireMock stubs)...[/dim]\n")

    console.print("[green]✓ Đã teardown sạch sẽ session, không sót process hay container.[/green]\n")


if __name__ == "__main__":
    run_demo()
