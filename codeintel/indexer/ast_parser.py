from __future__ import annotations

from dataclasses import dataclass
import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Node

GO_LANGUAGE = Language(tsgo.language())


@dataclass(slots=True)
class ExtractedFunction:
    id: str
    name: str
    package: str
    signature: str
    file_path: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    cyclomatic_complexity: int
    branch_count: int


def _extract_package(root_node: Node, source_bytes: bytes) -> str:
    for child in root_node.children:
        if child.type == "package_clause":
            for sub in child.children:
                if sub.type == "package_identifier":
                    return source_bytes[sub.start_byte:sub.end_byte].decode("utf-8")
    return "main"


def _calc_complexity_and_branches(node: Node) -> tuple[int, int]:
    branch_types = {
        "if_statement",
        "for_statement",
        "expression_switch_statement",
        "type_switch_statement",
        "expression_case",
        "type_case",
        "communication_case",
    }
    operator_types = {"&&", "||"}
    branches = 0
    stack = [node]
    while stack:
        curr = stack.pop()
        if curr.type in branch_types or curr.type in operator_types:
            branches += 1
        stack.extend(reversed(curr.children))

    return 1 + branches, branches


def parse_go_file(rel_path: str, source_code: str) -> list[ExtractedFunction]:
    parser = Parser(GO_LANGUAGE)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    pkg_name = _extract_package(root, source_bytes)
    results: list[ExtractedFunction] = []

    for child in root.children:
        if child.type in ("function_declaration", "method_declaration"):
            name = ""
            name_node = child.child_by_field_name("name")
            if name_node:
                name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
            else:
                for sub in child.children:
                    if sub.type in ("field_identifier", "identifier"):
                        name = source_bytes[sub.start_byte:sub.end_byte].decode("utf-8")
                        break

            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1
            func_id = f"{rel_path}::{name}::{start_line}"

            # Extract signature up to the body block
            body_node = child.child_by_field_name("body")
            if body_node:
                signature = source_bytes[child.start_byte:body_node.start_byte].decode("utf-8").strip()
            else:
                signature = source_bytes[child.start_byte:child.end_byte].decode("utf-8").strip()

            complexity, branches = _calc_complexity_and_branches(child)

            results.append(
                ExtractedFunction(
                    id=func_id,
                    name=name,
                    package=pkg_name,
                    signature=signature,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    start_byte=child.start_byte,
                    end_byte=child.end_byte,
                    cyclomatic_complexity=complexity,
                    branch_count=branches,
                )
            )
    return results
