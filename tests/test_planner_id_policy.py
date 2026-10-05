from agents.id_policy import validate_mutate_ids


MANIFEST = {
    "version": "catalog@test",
    "entities": {
        "item.cafe_kem_may": "a078b105-7140-47da-bb79-7ba228808a6f",
    },
}


def test_rejects_hardcoded_path_id_without_source():
    plan = {
        "test_cases": [
            {
                "id": "TC1",
                "type": "api",
                "test_data": {},
                "steps": [
                    {"step": 1, "action": "PUT /cms/items/item_12345", "data": {"quantity": 0}}
                ],
                "expected": {"status_code": 400},
            }
        ]
    }
    errors = validate_mutate_ids(plan, seed_manifest=None)
    assert errors
    assert any("TC1" in e for e in errors)


def test_allows_extract_then_put():
    plan = {
        "test_cases": [
            {
                "id": "TC2",
                "type": "api",
                "test_data": {},
                "extract": {"item_id": "data.id"},
                "steps": [
                    {"step": 1, "action": "POST /cms/items", "data": {"name": "x"}},
                    {"step": 2, "action": "PUT /cms/items/{{item_id}}", "data": {"quantity": 0}},
                ],
                "expected": {"status_code": 400},
            }
        ]
    }
    assert validate_mutate_ids(plan, seed_manifest=None) == []


def test_allows_seed_ref_and_manifest_uuid_in_test_data():
    plan = {
        "test_cases": [
            {
                "id": "TC3",
                "type": "api",
                "seed_ref": "item.cafe_kem_may",
                "test_data": {"item_id": "{{seed:item.cafe_kem_may}}"},
                "steps": [
                    {
                        "step": 1,
                        "action": "PUT /cms/items/{{item_id}}",
                        "data": {"quantity": 0},
                    }
                ],
                "expected": {"status_code": 400},
            }
        ]
    }
    assert validate_mutate_ids(plan, seed_manifest=MANIFEST) == []


def test_allows_user_supplied_uuid_in_test_data():
    uid = "a078b105-7140-47da-bb79-7ba228808a6f"
    plan = {
        "test_cases": [
            {
                "id": "TC4",
                "type": "api",
                "test_data": {"item_id": uid},
                "steps": [
                    {"step": 1, "action": "PUT /cms/items/{{item_id}}", "data": {}}
                ],
                "expected": {"status_code": 200},
            }
        ]
    }
    assert validate_mutate_ids(plan, seed_manifest=None) == []


def test_skips_non_api_and_get():
    plan = {
        "test_cases": [
            {
                "id": "TC5",
                "type": "ui",
                "steps": [{"step": 1, "action": "goto", "data": {}}],
            },
            {
                "id": "TC6",
                "type": "api",
                "test_data": {},
                "steps": [{"step": 1, "action": "GET /cms/items/item_12345", "data": {}}],
            },
        ]
    }
    assert validate_mutate_ids(plan, seed_manifest=None) == []


from types import SimpleNamespace
from agents.planner import planner_node
from agents.state import AgentState


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self._content)


def _base_state(**kwargs) -> AgentState:
    state: AgentState = {
        "user_request": "test update item",
        "documents": [],
        "code_paths": [],
        "openapi_spec": None,
        "messages": [],
        "test_plan": None,
        "generated_tests": [],
        "human_approved": False,
        "shared_context": {},
        "execution_result": None,
        "report_path": None,
        "final_summary": None,
        "current_step": "init",
        "error": None,
        "ui_headed": False,
    }
    state.update(kwargs)
    return state


BAD_PLAN = """
{
  "title": "t",
  "summary": "s",
  "scope": "api",
  "priority_order": ["critical"],
  "test_cases": [{
    "id": "TC_BAD",
    "title": "bad",
    "type": "api",
    "priority": "critical",
    "test_data": {},
    "steps": [{"step": 1, "action": "PUT /cms/items/item_12345", "data": {}}],
    "expected": {"status_code": 400},
    "status": "untested"
  }],
  "risks": [],
  "assumptions": [],
  "estimated_duration_min": 5,
  "created_by": "QC-Agent-Planner"
}
"""


def test_planner_node_rejects_invented_ids(monkeypatch):
    import agents.planner as planner_mod

    monkeypatch.setattr(planner_mod, "create_planner_llm", lambda: _FakeLLM(BAD_PLAN))
    result = planner_node(_base_state())
    assert result.get("test_plan") is None
    assert result.get("error")
    assert "invented" in result["error"].lower() or "TC_BAD" in result["error"]
