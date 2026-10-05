from pathlib import Path

from agents.api_executor import _run_single_api_test
from agents.patcher import CodePatcher, parse_search_replace_blocks


def test_closed_loop_test_patch_and_verify(tmp_path: Path):
    # 1. Microservice with a bug
    code_file = tmp_path / "app.py"
    code_file.write_text(
        """def calculate_discount(price: float, code: str) -> float:
    # BUG: None check missing
    return price * 0.1
""",
        encoding="utf-8",
    )

    # 2. Test case that exposes the bug
    # Mocking execution to simulate a failing test before fix
    failing_test = {
        "id": "TC_FIX_001",
        "title": "Calculate discount with invalid code",
        "steps": [{"step": 1, "action": "POST /api/v1/discount", "data": {"price": 100, "code": "INVALID"}}],
        "expected": {"status_code": 200, "body": {"discount": 0}},
    }

    # 3. LLM proposes Aider-style Search/Replace block
    llm_patch_response = f"""
Here is the fix for the discount calculation:

{code_file.name}
<<<<<<< SEARCH
def calculate_discount(price: float, code: str) -> float:
    # BUG: None check missing
    return price * 0.1
=======
def calculate_discount(price: float, code: str) -> float:
    if code != "VIP":
        return 0.0
    return price * 0.1
>>>>>>> REPLACE
"""

    blocks = parse_search_replace_blocks(llm_patch_response, default_filename=code_file.name)
    assert len(blocks) == 1

    # 4. Apply patch using CodePatcher
    patch_res = CodePatcher.patch_file(code_file, blocks)
    assert patch_res.success is True
    assert "+    if code != \"VIP\":" in patch_res.diff
    assert "-    # BUG: None check missing" in patch_res.diff

    # 5. Verify the code on disk now has the fix
    fixed_code = code_file.read_text(encoding="utf-8")
    assert "if code != \"VIP\":" in fixed_code
    assert "BUG: None check missing" not in fixed_code
