from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agents.state import AgentState
from codeintel.tools.branches import inspect_function_branches
from codeintel.tools.dependencies import get_external_dependencies
from codeintel.tools.scope import list_scope_functions
from config.settings import settings

logger = logging.getLogger(__name__)


def _resolve_repo_root(state: AgentState) -> Path:
    for cp in state.get("code_paths") or []:
        p = Path(cp)
        if p.is_dir():
            return p
        if p.is_file():
            for parent in p.parents:
                if (parent / "go.mod").exists():
                    return parent
            return p.parent
    return Path(".")


def codeintel_retriever_node(state: AgentState) -> dict[str, Any]:
    try:
        if not settings.enable_codeintel or not settings.codeintel_db_path.exists():
            return {"code_intelligence_summary": None}
        funcs_res = list_scope_functions(db_path=settings.codeintel_db_path)
        if not funcs_res.get("ok") or not funcs_res.get("data"):
            return {"code_intelligence_summary": None}

        top_funcs = funcs_res["data"][:5]  # Limit to top 5 functions
        summary_items = []
        repo_root = _resolve_repo_root(state)

        for fn in top_funcs:
            branches_res = inspect_function_branches(
                fn["id"],
                repo_root=repo_root,
                db_path=settings.codeintel_db_path,
            )
            deps_res = get_external_dependencies(
                fn["id"],
                db_path=settings.codeintel_db_path,
            )
            summary_items.append({
                "func_id": fn["id"],
                "name": fn["name"],
                "package": fn.get("package", ""),
                "complexity": fn["complexity"],
                "branches": (
                    branches_res.get("data", {}).get("branches", [])[:10]
                    if branches_res.get("ok")
                    else []
                ),
                "dependencies": (
                    deps_res.get("data", {}).get("callees", [])[:10]
                    if deps_res.get("ok")
                    else []
                ),
            })

        return {
            "code_intelligence_summary": {
                "target_functions": summary_items,
            }
        }
    except Exception as exc:
        logger.warning("codeintel_retriever_node error: %s", exc)
        return {"code_intelligence_summary": None}
