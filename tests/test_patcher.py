from pathlib import Path

from agents.patcher import (
    CodePatcher,
    EditBlock,
    find_similar_lines,
    parse_search_replace_blocks,
    perfect_replace,
    whitespace_tolerant_replace,
)


def test_parse_search_replace_blocks():
    text = """
Here is the fix for the bug:

app/main.py
<<<<<<< SEARCH
def hello():
    return "old"
=======
def hello():
    return "new"
>>>>>>> REPLACE

And another file:

utils.py
<<<<<<< SEARCH
x = 1
=======
x = 2
>>>>>>> REPLACE
"""
    blocks = parse_search_replace_blocks(text)
    assert len(blocks) == 2
    assert blocks[0].filename == "app/main.py"
    assert "return \"old\"" in blocks[0].search
    assert "return \"new\"" in blocks[0].replace
    assert blocks[1].filename == "utils.py"
    assert blocks[1].search.strip() == "x = 1"
    assert blocks[1].replace.strip() == "x = 2"


def test_perfect_replace():
    whole_lines = [
        "def add(a, b):\n",
        "    return a - b  # bug\n",
        "\n",
        "print(add(1, 2))\n",
    ]
    search_lines = ["    return a - b  # bug\n"]
    replace_lines = ["    return a + b\n"]

    res = perfect_replace(whole_lines, search_lines, replace_lines)
    assert res is not None
    assert "return a + b" in res
    assert "return a - b" not in res


def test_whitespace_tolerant_replace():
    # File has 8 spaces indent
    whole_lines = [
        "class Service:\n",
        "    def run(self):\n",
        "        x = 10\n",
        "        return x\n",
    ]
    # Search block has 4 spaces indent
    search_lines = [
        "    x = 10\n",
        "    return x\n",
    ]
    replace_lines = [
        "    x = 20\n",
        "    return x * 2\n",
    ]

    res = whitespace_tolerant_replace(whole_lines, search_lines, replace_lines)
    assert res is not None
    assert "        x = 20\n" in res
    assert "        return x * 2\n" in res


def test_find_similar_lines_suggestion():
    content = """def process_payment(amount, currency):
    if amount <= 0:
        raise ValueError("Invalid amount")
    return do_charge(amount)
"""
    search_with_typo = """def process_payment(amt, currency):
    if amt <= 0:
        raise ValueError("Invalid amount")
"""
    similar = find_similar_lines(search_with_typo, content)
    assert similar is not None
    assert "process_payment(amount, currency)" in similar


def test_patch_file_dry_run_and_apply(tmp_path: Path):
    target = tmp_path / "order_service.py"
    target.write_text(
        """def calculate_total(price, qty):
    return price * qty
""",
        encoding="utf-8",
    )

    block = EditBlock(
        filename=target.name,
        search="""def calculate_total(price, qty):
    return price * qty""",
        replace="""def calculate_total(price, qty):
    if qty <= 0:
        return 0
    return price * qty""",
    )

    # 1. Dry run: file should not be modified
    res_dry = CodePatcher.patch_file(target, [block], dry_run=True)
    assert res_dry.success is True
    assert "+    if qty <= 0:" in res_dry.diff
    assert "if qty <= 0" not in target.read_text(encoding="utf-8")

    # 2. Real apply: file should be modified
    res_real = CodePatcher.patch_file(target, [block], dry_run=False)
    assert res_real.success is True
    assert "if qty <= 0" in target.read_text(encoding="utf-8")
