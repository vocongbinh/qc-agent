from pathlib import Path
import pytest

from codeintel.indexer.ast_parser_ts import parse_ts_file
from codeintel.indexer.builder import build_index
from codeintel.tools.branches import inspect_function_branches
from codeintel.tools.scope import list_scope_functions


def test_parse_ts_file_extracts_classes_methods_and_arrows():
    code = """
import { Controller, Get, Param } from '@nestjs/common';

@Controller('users')
export class UserController {
    @Get(':id')
    async getUser(@Param('id') id: string) {
        if (!id) {
            throw new Error('ID is required');
        }
        return { id, name: 'Alice' };
    }
}

export function helper(a: number, b: number): number {
    return a > b ? a : b;
}

export const multiply = (x: number, y: number) => {
    if (x === 0 || y === 0) {
        return 0;
    }
    return x * y;
};
"""
    funcs = parse_ts_file("src/user.controller.ts", code)
    names = {f.name: f for f in funcs}

    assert "getUser" in names
    fn_user = names["getUser"]
    assert fn_user.package == "UserController"
    assert fn_user.cyclomatic_complexity == 2  # 1 base + 1 if
    assert fn_user.branch_count == 1
    assert "async getUser" in fn_user.signature

    assert "helper" in names
    fn_helper = names["helper"]
    assert fn_helper.cyclomatic_complexity == 2  # 1 base + 1 ternary
    assert fn_helper.branch_count == 1

    assert "multiply" in names
    fn_mult = names["multiply"]
    assert fn_mult.cyclomatic_complexity == 3  # 1 base + 1 if + 1 ||
    assert fn_mult.branch_count == 2


def test_build_index_typescript_and_jit_branches(tmp_path: Path):
    ts_repo = tmp_path / "ts_project"
    src_dir = ts_repo / "src"
    src_dir.mkdir(parents=True)

    controller_code = """
export class ItemService {
    calculatePrice(qty: number, unitPrice: number): number {
        if (qty <= 0) {
            return 0;
        }
        if (qty > 100) {
            return qty * unitPrice * 0.9;
        }
        return qty * unitPrice;
    }
}
"""
    (src_dir / "item.service.ts").write_text(controller_code, encoding="utf-8")

    # Add mock openapi.json
    openapi_json = """{
  "openapi": "3.0.0",
  "paths": {
    "/items/price": {
      "post": {
        "operationId": "calculatePrice",
        "responses": { "200": { "description": "ok" } }
      }
    }
  }
}"""
    (ts_repo / "openapi.json").write_text(openapi_json, encoding="utf-8")

    db_path = tmp_path / "ts_kuzu"
    stats = build_index(repo_root=ts_repo, db_path=db_path, lang="ts")

    assert stats["files_indexed"] == 1
    assert stats["functions_indexed"] == 1
    assert stats["endpoints_indexed"] == 1

    # Test list_scope_functions
    scope_res = list_scope_functions(db_path=db_path)
    assert scope_res["ok"] is True
    assert len(scope_res["data"]) == 1
    fn_info = scope_res["data"][0]
    assert fn_info["name"] == "calculatePrice"
    assert fn_info["complexity"] == 3

    # Test JIT branch inspection
    branches_res = inspect_function_branches(fn_info["id"], repo_root=ts_repo, db_path=db_path)
    assert branches_res["ok"] is True
    assert len(branches_res["data"]["branches"]) == 2
    conds = [b["condition"] for b in branches_res["data"]["branches"]]
    assert "qty <= 0" in conds
    assert "qty > 100" in conds
