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
    # Map functions by file_path for fast lookup
    file_to_funcs: dict[str, list[ExtractedFunction]] = {}
    for fn in functions:
        file_to_funcs.setdefault(fn.file_path, []).append(fn)

    calls: list[dict[str, Any]] = []

    for occ in occurrences:
        if occ.is_definition:
            continue
        funcs_in_file = file_to_funcs.get(occ.file_path, [])
        for fn in funcs_in_file:
            # Check if line of occurrence falls within function span
            if fn.start_line <= occ.start_line <= fn.end_line:
                is_ext = True
                if internal_module_prefix and occ.symbol.startswith(internal_module_prefix):
                    is_ext = False
                elif not internal_module_prefix and not occ.symbol.startswith("builtin"):
                    is_ext = False

                calls.append({
                    "caller_id": fn.id,
                    "callee_symbol": occ.symbol,
                    "line_number": occ.start_line,
                    "is_external": is_ext,
                })
                break

    return calls
