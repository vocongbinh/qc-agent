from unittest.mock import MagicMock
import pytest

from agents.api_executor import _run_single_api_test
from sandbox import session


def test_api_executor_triggers_stub_scenario_and_resets(monkeypatch):
    mock_provider = MagicMock()
    monkeypatch.setattr(session, "_ACTIVE_STUB_PROVIDER", mock_provider)

    test_case = {
        "id": "TC_DOWNSTREAM_001",
        "stub_scenario": "inventory_unavailable",
        "steps": [
            {
                "step": 1,
                "action": "GET /api/v1/orders",
            }
        ],
        "expected": {
            "status_code": 200
        }
    }

    # Mock httpx client response
    class MockResponse:
        status_code = 200
        text = '{"status": "ok"}'
        def json(self):
            return {"status": "ok"}

    class MockHttpxClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def request(self, *args, **kwargs):
            return MockResponse()

    monkeypatch.setattr("httpx.Client", MockHttpxClient)

    result = _run_single_api_test(test_case, base_url="http://127.0.0.1:8000")

    assert result["status"] == "passed"
    mock_provider.load_scenario.assert_called_once_with("inventory_unavailable")
    mock_provider.reset.assert_called_once()
