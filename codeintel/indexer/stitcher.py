from __future__ import annotations

from typing import Any

from codeintel.indexer.ast_parser import ExtractedFunction
from codeintel.indexer.scip_reader import ScipOccurrence


def stitch_calls_and_types(
    functions: list[ExtractedFunction],
    occurrences: list[ScipOccurrence],
    internal_module_prefix: str = "",
) -> list[dict[str, Any]]:
    """Correlates SCIP occurrences with enclosing AST function definitions.

    Filters out definition occurrences, attributes remaining references to
    their enclosing function caller (by file and line range), and flags calls
    as internal or external based on module prefix.
    """
    # Map functions by normalized file_path for fast lookup
    file_to_funcs: dict[str, list[ExtractedFunction]] = {}
    for fn in functions:
        norm_path = fn.file_path.replace("\\", "/").lstrip("./")
        file_to_funcs.setdefault(norm_path, []).append(fn)

    calls: list[dict[str, Any]] = []

    for occ in occurrences:
        if occ.is_definition:
            continue
        norm_occ_path = occ.file_path.replace("\\", "/").lstrip("./")
        candidates = file_to_funcs.get(norm_occ_path, [])
        # Find matching enclosing functions and pick innermost (smallest span)
        matching_funcs = [
            fn for fn in candidates
            if fn.start_line <= occ.start_line <= fn.end_line
        ]
        if not matching_funcs:
            continue

        matching_funcs.sort(key=lambda f: f.end_line - f.start_line)
        best_fn = matching_funcs[0]

        is_ext = True
        if occ.symbol.startswith("local "):
            is_ext = False
        elif internal_module_prefix and occ.symbol.startswith(internal_module_prefix):
            is_ext = False
        elif not internal_module_prefix and not occ.symbol.startswith("builtin"):
            is_ext = False

        calls.append({
            "caller_id": best_fn.id,
            "callee_symbol": occ.symbol,
            "line_number": occ.start_line,
            "is_external": is_ext,
        })
    return calls
