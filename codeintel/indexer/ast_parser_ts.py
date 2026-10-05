from __future__ import annotations

import tree_sitter_typescript as tst
from tree_sitter import Language, Node, Parser

from codeintel.indexer.ast_parser import ExtractedFunction

TS_LANGUAGE = Language(tst.language_typescript())
TSX_LANGUAGE = Language(tst.language_tsx())

BRANCH_TYPES = {
    "if_statement",
    "for_statement",
    "for_in_statement",
    "while_statement",
    "do_statement",
    "switch_case",
    "catch_clause",
    "ternary_expression",
}
OPERATOR_TYPES = {"&&", "||", "??"}


def _calc_complexity_and_branches(node: Node) -> tuple[int, int]:
    branches = 0
    stack = [node]
    while stack:
        curr = stack.pop()
        if curr.type in BRANCH_TYPES or curr.type in OPERATOR_TYPES:
            branches += 1
        stack.extend(reversed(curr.children))
    return 1 + branches, branches

def _extract_calls_from_ts_node(node: Node, source_bytes: bytes) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    stack = [node]
    while stack:
        curr = stack.pop()
        if curr.type == "call_expression":
            fn_node = curr.child_by_field_name("function")
            if fn_node:
                prop_node = fn_node.child_by_field_name("property")
                name = (
                    source_bytes[prop_node.start_byte:prop_node.end_byte].decode("utf-8", errors="replace")
                    if prop_node
                    else source_bytes[fn_node.start_byte:fn_node.end_byte].decode("utf-8", errors="replace")
                )
                if name:
                    calls.append((name, curr.start_point[0] + 1))
        stack.extend(reversed(curr.children))
    return calls


def parse_ts_file(rel_path: str, source_code: str) -> list[ExtractedFunction]:
    lang = TSX_LANGUAGE if rel_path.endswith(".tsx") else TS_LANGUAGE
    parser = Parser(lang)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    results: list[ExtractedFunction] = []

    def traverse(node: Node, class_name: str = "") -> None:
        if node.type in ("class_declaration", "class"):
            name_node = node.child_by_field_name("name")
            c_name = (
                source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                if name_node
                else ""
            )
            for child in node.children:
                traverse(child, c_name)
            return

        if node.type in ("method_definition", "function_declaration"):
            name_node = node.child_by_field_name("name")
            name = (
                source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                if name_node
                else ""
            )
            if not name:
                return

            body_node = node.child_by_field_name("body")
            if body_node:
                raw_sig = source_bytes[node.start_byte:body_node.start_byte].decode("utf-8", errors="replace").strip()
            else:
                raw_sig = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()

            # Clean decorators from signature for readability
            sig_lines = [l for l in raw_sig.splitlines() if not l.strip().startswith("@")]
            signature = " ".join(" ".join(sig_lines).split())

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            func_id = f"{rel_path}::{class_name + '.' if class_name else ''}{name}::{start_line}"
            complexity, branches = _calc_complexity_and_branches(node)
            func_calls = _extract_calls_from_ts_node(node, source_bytes)

            results.append(
                ExtractedFunction(
                    id=func_id,
                    name=name,
                    package=class_name or "default",
                    signature=signature,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    cyclomatic_complexity=complexity,
                    branch_count=branches,
                    calls=func_calls,
                )
            )
            return

        if node.type == "variable_declarator":
            val_node = node.child_by_field_name("value")
            if val_node and val_node.type in ("arrow_function", "function"):
                name_node = node.child_by_field_name("name")
                name = (
                    source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                    if name_node
                    else ""
                )
                if name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    func_id = f"{rel_path}::{name}::{start_line}"
                    body_node = val_node.child_by_field_name("body")
                    if body_node:
                        raw_sig = source_bytes[node.start_byte:body_node.start_byte].decode("utf-8", errors="replace").strip()
                    else:
                        raw_sig = source_bytes[node.start_byte:val_node.end_byte].decode("utf-8", errors="replace").strip()
                    signature = " ".join(" ".join(raw_sig.splitlines()).split())
                    complexity, branches = _calc_complexity_and_branches(val_node)
                    func_calls = _extract_calls_from_ts_node(val_node, source_bytes)

                    results.append(
                        ExtractedFunction(
                            id=func_id,
                            name=name,
                            package="default",
                            signature=signature,
                            file_path=rel_path,
                            start_line=start_line,
                            end_line=end_line,
                            start_byte=val_node.start_byte,
                            end_byte=val_node.end_byte,
                            cyclomatic_complexity=complexity,
                            branch_count=branches,
                            calls=func_calls,
                        )
                    )
                    return

        for child in node.children:
            traverse(child, class_name)

    traverse(root)
    return results
