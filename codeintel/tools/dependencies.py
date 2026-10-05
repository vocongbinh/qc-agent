from __future__ import annotations

from pathlib import Path
from typing import Any
from codeintel.store.db import execute_query
from config.settings import settings


def get_external_dependencies(
    func_id: str,
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
        check = execute_query(
            db_path,
            "MATCH (fn:Function {id: $id}) RETURN fn.id AS id",
            {"id": func_id},
        )
        if not check:
            return {
                "ok": False,
                "data": None,
                "error": f"Function {func_id} not found.",
            }

        rows = execute_query(
            db_path,
            """MATCH (caller:Function {id: $id})-[c:CALLS]->(callee:Function)
               RETURN callee.id AS callee_id, callee.name AS name,
                      c.line_number AS line, c.line_number AS line_number,
                      c.is_external AS is_external""",
            {"id": func_id},
        )
        return {
            "ok": True,
            "data": {
                "func_id": func_id,
                "callees": rows,
            },
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "data": None,
            "error": f"Database error: {exc}",
        }
