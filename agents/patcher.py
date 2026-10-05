"""Code Patcher module for QC Agent.

Inspired by Aider's search/replace algorithm:
- Uses SEARCH/REPLACE blocks for surgical, safe code edits.
- Sliding-window exact line matching (perfect_replace).
- Whitespace-tolerant matching fallback.
- SequenceMatcher (did_you_mean) suggestions when matching fails.
- Generates clean unified diffs for human review and git merge.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class EditBlock:
    filename: str
    search: str
    replace: str


@dataclass
class PatchResult:
    filename: str
    success: bool
    error: str | None = None
    diff: str = ""


# Regex to match Aider-style SEARCH/REPLACE blocks
# Supports optional filename on line preceding <<<<<<< SEARCH, or inside fence
_BLOCK_PATTERN = re.compile(
    r"(?:(?P<filename>[\w\-./\\]+\.[a-zA-Z0-9_]+)\s*\n)?"
    r"<{5,9}\s*SEARCH\s*\n"
    r"(?P<search>.*?)"
    r"={5,9}\s*\n"
    r"(?P<replace>.*?)"
    r">{5,9}\s*REPLACE",
    re.DOTALL,
)


def parse_search_replace_blocks(
    text: str, default_filename: str | None = None
) -> list[EditBlock]:
    """Extract all SEARCH/REPLACE blocks from LLM markdown response."""
    blocks: list[EditBlock] = []
    # Strip backticks code fences if wrapped
    cleaned = text
    for m in _BLOCK_PATTERN.finditer(cleaned):
        fn = m.group("filename") or default_filename or ""
        search = m.group("search")
        replace = m.group("replace")
        blocks.append(
            EditBlock(
                filename=fn.strip(),
                search=search,
                replace=replace,
            )
        )
    return blocks


def _prep_lines(content: str) -> list[str]:
    if content and not content.endswith("\n"):
        content += "\n"
    return content.splitlines(keepends=True)


def perfect_replace(whole_lines: list[str], search_lines: list[str], replace_lines: list[str]) -> str | None:
    """Sliding-window exact line matching."""
    s_len = len(search_lines)
    if s_len == 0:
        return None

    s_tup = tuple(search_lines)
    for i in range(len(whole_lines) - s_len + 1):
        if tuple(whole_lines[i : i + s_len]) == s_tup:
            res = whole_lines[:i] + replace_lines + whole_lines[i + s_len :]
            return "".join(res)
    return None


def whitespace_tolerant_replace(
    whole_lines: list[str], search_lines: list[str], replace_lines: list[str]
) -> str | None:
    """Match lines by stripping leading/trailing whitespace, and preserve target indentation."""
    s_len = len(search_lines)
    if s_len == 0:
        return None

    stripped_search = [l.strip() for l in search_lines]
    for i in range(len(whole_lines) - s_len + 1):
        candidate = [l.strip() for l in whole_lines[i : i + s_len]]
        if candidate == stripped_search:
            # Found match! Adjust indentation of replace_lines based on the first matched line
            first_orig = whole_lines[i]
            orig_indent = first_orig[: len(first_orig) - len(first_orig.lstrip())]
            first_search = search_lines[0]
            search_indent = first_search[: len(first_search) - len(first_search.lstrip())]

            adjusted_replace: list[str] = []
            for r_line in replace_lines:
                if r_line.strip() == "":
                    adjusted_replace.append("\n")
                elif r_line.startswith(search_indent):
                    # Replace search_indent prefix with orig_indent
                    adjusted_replace.append(orig_indent + r_line[len(search_indent) :])
                else:
                    adjusted_replace.append(orig_indent + r_line.lstrip())

            res = whole_lines[:i] + adjusted_replace + whole_lines[i + s_len :]
            return "".join(res)
    return None


def find_similar_lines(search: str, content: str, cutoff: float = 0.6) -> str | None:
    """Use SequenceMatcher to find the most similar code snippet when exact search fails."""
    search_lines = search.strip().splitlines()
    content_lines = content.strip().splitlines()
    if not search_lines or not content_lines:
        return None

    s_len = len(search_lines)
    search_str = "\n".join(search_lines)
    best_ratio = 0.0
    best_chunk: list[str] = []

    for i in range(max(1, len(content_lines) - s_len + 1)):
        chunk = content_lines[i : i + s_len]
        chunk_str = "\n".join(chunk)
        ratio = difflib.SequenceMatcher(None, chunk_str, search_str).ratio()
        if ratio > best_ratio and ratio >= cutoff:
            best_ratio = ratio
            best_chunk = chunk

    if best_chunk:
        return "\n".join(best_chunk)
    return None


class CodePatcher:
    """Applies surgical Search/Replace blocks to files and generates diffs."""

    @staticmethod
    def apply_block_to_string(content: str, block: EditBlock) -> tuple[str, str | None]:
        """Apply a single edit block to content. Returns (new_content, error_message)."""
        whole_lines = _prep_lines(content)
        search_lines = _prep_lines(block.search)
        replace_lines = _prep_lines(block.replace)

        # 1. Exact match
        res = perfect_replace(whole_lines, search_lines, replace_lines)
        if res is not None:
            return res, None

        # 2. Whitespace-tolerant match
        res = whitespace_tolerant_replace(whole_lines, search_lines, replace_lines)
        if res is not None:
            return res, None

        # 3. Match failed: construct actionable error with did_you_mean hint
        similar = find_similar_lines(block.search, content)
        err = f"SEARCH block failed to match in {block.filename or 'file'}.\n"
        if similar:
            err += f"Did you mean to match this actual code?\n```\n{similar}\n```"
        else:
            err += "Could not find a similar matching block in the target file."
        return content, err

    @classmethod
    def patch_file(
        cls, file_path: Path | str, blocks: list[EditBlock], dry_run: bool = False
    ) -> PatchResult:
        """Apply a list of blocks to a specific file on disk."""
        p = Path(file_path).resolve()
        if not p.exists():
            return PatchResult(
                filename=str(file_path),
                success=False,
                error=f"File not found: {p}",
            )

        orig_content = p.read_text(encoding="utf-8")
        current_content = orig_content
        errors: list[str] = []

        for block in blocks:
            current_content, err = cls.apply_block_to_string(current_content, block)
            if err:
                errors.append(err)

        if errors:
            return PatchResult(
                filename=str(file_path),
                success=False,
                error="\n\n".join(errors),
            )

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                orig_content.splitlines(keepends=True),
                current_content.splitlines(keepends=True),
                fromfile=f"a/{p.name}",
                tofile=f"b/{p.name}",
            )
        )
        diff_str = "".join(diff_lines)

        if not dry_run and current_content != orig_content:
            p.write_text(current_content, encoding="utf-8")

        return PatchResult(
            filename=str(file_path),
            success=True,
            diff=diff_str,
        )


PATCHER_SYSTEM_PROMPT = """You are an expert AI Software Engineer fixing a bug in a codebase.
All modifications to files MUST use the exact *SEARCH/REPLACE block* format shown below.
Do NOT rewrite the entire file! Only output the minimal lines needed to fix the bug.

Format:
[path/to/file.ext]
<<<<<<< SEARCH
[exact lines currently in file]
=======
[new lines to replace with]
>>>>>>> REPLACE

Example:
app/services/order.py
<<<<<<< SEARCH
def calculate_discount(voucher):
    return voucher.rate * total
=======
def calculate_discount(voucher):
    if not voucher or voucher.is_expired:
        return 0
    return voucher.rate * total
>>>>>>> REPLACE
"""
