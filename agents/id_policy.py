from __future__ import annotations

import re
from typing import Any

_MUTATE_METHODS = {"PUT", "PATCH", "DELETE"}
_PATH_ID_RE = re.compile(
    r"/(?:cms|api|internal)/[\w\-./]*?/([A-Za-z0-9_\-]{6,})(?:/|$|\?)"
)
_TEMPLATE_RE = re.compile(r"\{\{(\w+)\}\}")
_SEED_TEMPLATE_RE = re.compile(r"\{\{seed:([\w.\-]+)\}\}")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_COLLECTION_SEGMENTS = {
    "items",
    "stores",
    "categories",
    "status",
    "restore",
    "upload",
    "health",
    "variants",
    "modifiers",
}


def _parse_method_path(action: str) -> tuple[str, str]:
    parts = (action or "").strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
    }:
        return parts[0].upper(), parts[1]
    return "GET", action or ""


def _collect_known_keys(
    case: dict[str, Any], seed_manifest: dict[str, Any] | None
) -> set[str]:
    keys: set[str] = set((case.get("test_data") or {}).keys())
    extract = case.get("extract") or (case.get("expected") or {}).get("extract") or {}
    keys.update(extract.keys())
    for step in case.get("steps") or []:
        step_ex = (step.get("expected") or {}).get("extract") or step.get("extract") or {}
        keys.update(step_ex.keys())
    if case.get("seed_ref"):
        keys.add("item_id")
    if seed_manifest and seed_manifest.get("entities"):
        keys.add("item_id")
    return keys


def _literal_path_ids(path: str) -> list[str]:
    """Return path segments that look like resource IDs and are not templates."""
    if "{{" in path:
        return []
    found: list[str] = []
    for m in _PATH_ID_RE.finditer(path):
        seg = m.group(1)
        if seg.lower() in _COLLECTION_SEGMENTS:
            continue
        found.append(seg)
    return found


def validate_mutate_ids(
    plan: dict[str, Any],
    seed_manifest: dict[str, Any] | None = None,
) -> list[str]:
    """Return list of human-readable errors. Empty list = OK."""
    errors: list[str] = []
    entities = (seed_manifest or {}).get("entities") or {}

    for case in plan.get("test_cases") or []:
        if (case.get("type") or "api").lower() not in {"api", "integration"}:
            continue
        case_id = case.get("id") or "?"
        known = _collect_known_keys(case, seed_manifest)
        seed_ref = case.get("seed_ref")
        if seed_ref and seed_ref not in entities and seed_manifest is not None:
            errors.append(
                f"{case_id}: seed_ref '{seed_ref}' not in seed_manifest.entities"
            )

        test_data = case.get("test_data") or {}
        for step in case.get("steps") or []:
            method, path = _parse_method_path(step.get("action") or "")
            if method not in _MUTATE_METHODS:
                continue

            blob = path + str(step.get("data") or "")
            for key in _TEMPLATE_RE.findall(path):
                if key in known or key in test_data:
                    continue
                errors.append(
                    f"{case_id}: mutate path uses {{{{{key}}}}} but key not in "
                    f"test_data/extract/seed_ref"
                )

            for seed_key in _SEED_TEMPLATE_RE.findall(blob):
                if seed_key not in entities:
                    errors.append(
                        f"{case_id}: unknown seed template '{{{{seed:{seed_key}}}}}'"
                    )

            allowed_vals = {str(v) for v in test_data.values()}
            allowed_vals.update(str(v) for v in entities.values())
            for lit in _literal_path_ids(path):
                if lit in allowed_vals:
                    continue
                errors.append(
                    f"{case_id}: invented path id '{lit}' on {method} {path}; "
                    f"use seed_ref, extract, or user test_data"
                )
    return errors
