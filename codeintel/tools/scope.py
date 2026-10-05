from __future__ import annotations

from pathlib import Path
from typing import Any
from codeintel.store.db import execute_query
from config.settings import settings


def list_scope_functions(
    package: str = "",
    min_complexity: int = 1,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    db_target = Path(db_path or settings.codeintel_db_path)
    if str(db_target) != ":memory:" and not db_target.exists():
        return {
            "ok": False,
            "data": None,
            "error": f"Database does not exist at {db_target}",
        }

    try:
        if package:
            query = """MATCH (fn:Function)
                       WHERE fn.cyclomatic_complexity >= $min_complexity AND fn.package = $package
                       RETURN fn.id AS id, fn.name AS name, fn.package AS package,
                              fn.cyclomatic_complexity AS complexity, fn.branch_count AS branches
                       ORDER BY fn.cyclomatic_complexity DESC"""
            params = {"min_complexity": min_complexity, "package": package}
        else:
            query = """MATCH (fn:Function)
                       WHERE fn.cyclomatic_complexity >= $min_complexity
                       RETURN fn.id AS id, fn.name AS name, fn.package AS package,
                              fn.cyclomatic_complexity AS complexity, fn.branch_count AS branches
                       ORDER BY fn.cyclomatic_complexity DESC"""
            params = {"min_complexity": min_complexity}

        rows = execute_query(db_path, query, params)
        return {
            "ok": True,
            "data": rows,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "data": None,
            "error": f"Database error: {exc}",
        }
