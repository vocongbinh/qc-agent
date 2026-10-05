# Sandbox Runner + ID Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop LLM-invented resource IDs in mutate API plans, then add an optional Postgres sandbox session with SQL seed + `seed_manifest` so QC Agent can run reproducible catalog-service tests without flaky 404s.

**Architecture:** Phase 0 hardens Planner/Executor ID policy on the existing `external` mode (no Docker required). Phases 1+3 add a session-scoped Postgres `SandboxProvider` (Docker Compose project with random name), migrate/seed once, emit `seed_manifest` into `AgentState`, and resolve `seed_ref` in the HTTP executor. Phase 2 optional `full_local` app subprocess and Phase 5 TEMPLATE/warm-path come after MVP works.

**Tech Stack:** Python 3.12, LangGraph, httpx, PyYAML, Docker Compose, Postgres 16, catalog-service Drizzle SQL seeds. No Mongo. Redis provider optional (P1.b).

**Spec:** `docs/superpowers/specs/2026-10-05-sandbox-runner-design.md`

**Execution order:** Tasks 1–5 = Phase 0 (shippable alone) → Tasks 6–10 = Phase 1+3 MVP → Tasks 11–12 = Phase 2 → Task 13 = Phase 5.

---

### File Structure Map

```text
qc-agent/
├── requirements.txt                      # pyyaml already present; add psycopg[binary] for reset/seed
├── config/settings.py                    # sandbox_mode, agent_yaml_path defaults
├── agent.yaml                            # default external-mode sample (catalog aliases)
├── sandbox/
│   ├── __init__.py
│   ├── config.py                         # load/validate agent.yaml → SandboxConfig
│   ├── provider.py                       # Protocol + factory
│   ├── postgres.py                       # Docker Compose Postgres provider
│   ├── session.py                        # context manager start/seed/reset/stop
│   ├── seed.py                           # migrate + apply SQL + build manifest
│   └── app_orchestrator.py               # Phase 2 only
├── agents/
│   ├── state.py                          # + seed_manifest
│   ├── id_policy.py                      # validate mutate IDs (pure, no LLM)
│   ├── planner.py                        # prompt rules + manifest inject + validate
│   ├── api_executor.py                   # resolve seed_ref into context
│   └── graph.py                          # optional sandbox bootstrap later (Task 11)
├── environments/
│   └── docker-compose.sandbox-pg.yml     # Postgres-only template for provider
└── tests/
    ├── test_planner_id_policy.py
    ├── test_seed_ref_executor.py
    ├── test_sandbox_config.py
    ├── test_sandbox_provider.py          # needs Docker; mark integration
    └── test_seed_manifest.py
```

---

### Task 1: AgentState + settings hooks for seed_manifest

**Files:**
- Modify: `agents/state.py`
- Modify: `config/settings.py`
- Test: `tests/test_sandbox_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sandbox_config.py
from pathlib import Path
from agents.state import AgentState
from config.settings import settings


def test_agent_state_has_seed_manifest_annotation():
    assert "seed_manifest" in AgentState.__annotations__


def test_sandbox_settings_defaults():
    assert hasattr(settings, "sandbox_mode")
    assert settings.sandbox_mode == "external"
    assert hasattr(settings, "agent_yaml_path")
    assert isinstance(settings.agent_yaml_path, Path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sandbox_config.py -v`
Expected: FAIL (`seed_manifest` missing and/or settings attrs missing)

- [ ] **Step 3: Minimal implementation**

In `agents/state.py`, inside `AgentState`, after `code_intelligence_summary`:

```python
    seed_manifest: NotRequired[dict[str, Any] | None]
```

In `config/settings.py`, after codeintel settings:

```python
    # Sandbox / ID policy
    sandbox_mode: str = "external"  # external | db_only | full_local
    agent_yaml_path: Path = project_root / "agent.yaml"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sandbox_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/state.py config/settings.py tests/test_sandbox_config.py
git commit -m "feat(sandbox): add seed_manifest state and sandbox settings defaults"
```

---

### Task 2: Pure ID policy validator

**Files:**
- Create: `agents/id_policy.py`
- Test: `tests/test_planner_id_policy.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_planner_id_policy.py
from agents.id_policy import validate_mutate_ids


MANIFEST = {
    "version": "catalog@test",
    "entities": {
        "item.cafe_kem_may": "a078b105-7140-47da-bb79-7ba228808a6f",
    },
}


def test_rejects_hardcoded_path_id_without_source():
    plan = {
        "test_cases": [
            {
                "id": "TC1",
                "type": "api",
                "test_data": {},
                "steps": [
                    {"step": 1, "action": "PUT /cms/items/item_12345", "data": {"quantity": 0}}
                ],
                "expected": {"status_code": 400},
            }
        ]
    }
    errors = validate_mutate_ids(plan, seed_manifest=None)
    assert errors
    assert any("TC1" in e for e in errors)


def test_allows_extract_then_put():
    plan = {
        "test_cases": [
            {
                "id": "TC2",
                "type": "api",
                "test_data": {},
                "extract": {"item_id": "data.id"},
                "steps": [
                    {"step": 1, "action": "POST /cms/items", "data": {"name": "x"}},
                    {"step": 2, "action": "PUT /cms/items/{{item_id}}", "data": {"quantity": 0}},
                ],
                "expected": {"status_code": 400},
            }
        ]
    }
    assert validate_mutate_ids(plan, seed_manifest=None) == []


def test_allows_seed_ref_and_manifest_uuid_in_test_data():
    plan = {
        "test_cases": [
            {
                "id": "TC3",
                "type": "api",
                "seed_ref": "item.cafe_kem_may",
                "test_data": {"item_id": "{{seed:item.cafe_kem_may}}"},
                "steps": [
                    {
                        "step": 1,
                        "action": "PUT /cms/items/{{item_id}}",
                        "data": {"quantity": 0},
                    }
                ],
                "expected": {"status_code": 400},
            }
        ]
    }
    assert validate_mutate_ids(plan, seed_manifest=MANIFEST) == []


def test_allows_user_supplied_uuid_in_test_data():
    uid = "a078b105-7140-47da-bb79-7ba228808a6f"
    plan = {
        "test_cases": [
            {
                "id": "TC4",
                "type": "api",
                "test_data": {"item_id": uid},
                "steps": [
                    {"step": 1, "action": f"PUT /cms/items/{{{{{ 'item_id' }}}}}", "data": {}}
                ],
                "expected": {"status_code": 200},
            }
        ]
    }
    # Use explicit template form:
    plan["test_cases"][0]["steps"][0]["action"] = "PUT /cms/items/{{item_id}}"
    assert validate_mutate_ids(plan, seed_manifest=None) == []


def test_skips_non_api_and_get():
    plan = {
        "test_cases": [
            {
                "id": "TC5",
                "type": "ui",
                "steps": [{"step": 1, "action": "goto", "data": {}}],
            },
            {
                "id": "TC6",
                "type": "api",
                "test_data": {},
                "steps": [{"step": 1, "action": "GET /cms/items/item_12345", "data": {}}],
            },
        ]
    }
    assert validate_mutate_ids(plan, seed_manifest=None) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_planner_id_policy.py -v`
Expected: FAIL `ModuleNotFoundError: agents.id_policy`

- [ ] **Step 3: Implement `agents/id_policy.py`**

```python
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


def _parse_method_path(action: str) -> tuple[str, str]:
    parts = (action or "").strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        return parts[0].upper(), parts[1]
    return "GET", action or ""


def _collect_known_keys(case: dict[str, Any], seed_manifest: dict[str, Any] | None) -> set[str]:
    keys: set[str] = set((case.get("test_data") or {}).keys())
    extract = case.get("extract") or (case.get("expected") or {}).get("extract") or {}
    keys.update(extract.keys())
    # Per-step extract maps (optional)
    for step in case.get("steps") or []:
        step_ex = (step.get("expected") or {}).get("extract") or step.get("extract") or {}
        keys.update(step_ex.keys())
    if case.get("seed_ref"):
        keys.add("item_id")  # conventional binding; also allow seed templates
    if seed_manifest and seed_manifest.get("entities"):
        keys.add("item_id")
    return keys


def _literal_path_ids(path: str) -> list[str]:
    """Return path segments that look like resource IDs and are not templates."""
    if "{{" in path:
        return []
    found = []
    for m in _PATH_ID_RE.finditer(path):
        seg = m.group(1)
        # skip pure collection verbs already handled by regex; reject obvious placeholders
        if seg.lower() in {"items", "stores", "categories", "status", "restore", "upload", "health"}:
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
            errors.append(f"{case_id}: seed_ref '{seed_ref}' not in seed_manifest.entities")

        test_data = case.get("test_data") or {}
        for step in case.get("steps") or []:
            method, path = _parse_method_path(step.get("action") or "")
            if method not in _MUTATE_METHODS:
                continue

            # Templates must resolve from known keys or seed:
            for key in _TEMPLATE_RE.findall(path):
                if key not in known and f"seed:{key}" not in {f"seed:{k}" for k in entities}:
                    # allow if test_data has concrete value for key
                    if key not in test_data:
                        errors.append(
                            f"{case_id}: mutate path uses {{{{{key}}}}} but key not in "
                            f"test_data/extract/seed_ref"
                        )

            for seed_key in _SEED_TEMPLATE_RE.findall(path + str(step.get("data") or "")):
                if seed_key not in entities:
                    errors.append(f"{case_id}: unknown seed template '{{{{seed:{seed_key}}}}}'")

            for lit in _literal_path_ids(path):
                # Allowed only if equals a user-supplied UUID in test_data values or manifest value
                allowed_vals = {str(v) for v in test_data.values()}
                allowed_vals.update(str(v) for v in entities.values())
                if lit in allowed_vals or _UUID_RE.match(lit) and lit in allowed_vals:
                    continue
                if _UUID_RE.match(lit) and lit in allowed_vals:
                    continue
                # Literal non-template ID in path without being a known supplied/manifest value
                if lit not in allowed_vals:
                    errors.append(
                        f"{case_id}: invented path id '{lit}' on {method} {path}; "
                        f"use seed_ref, extract, or user test_data"
                    )
    return errors
```

Fix the slightly redundant UUID checks when implementing — keep one clear branch: literal path IDs are allowed only if the literal string is present in `test_data.values()` or `entities.values()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_planner_id_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/id_policy.py tests/test_planner_id_policy.py
git commit -m "feat(sandbox): add mutate ID policy validator"
```

---

### Task 3: Wire validator + prompt rules into Planner

**Files:**
- Modify: `agents/planner.py`
- Test: `tests/test_planner_id_policy.py` (add integration-style unit against `planner_node` with monkeypatched LLM)

- [ ] **Step 1: Write failing test for planner rejection**

```python
# append to tests/test_planner_id_policy.py
from types import SimpleNamespace
from agents.planner import planner_node
from agents.state import AgentState


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self._content)


def _base_state(**kwargs) -> AgentState:
    state: AgentState = {
        "user_request": "test update item",
        "documents": [],
        "code_paths": [],
        "openapi_spec": None,
        "messages": [],
        "test_plan": None,
        "generated_tests": [],
        "human_approved": False,
        "shared_context": {},
        "execution_result": None,
        "report_path": None,
        "final_summary": None,
        "current_step": "init",
        "error": None,
        "ui_headed": False,
    }
    state.update(kwargs)
    return state


BAD_PLAN = """
{
  "title": "t",
  "summary": "s",
  "scope": "api",
  "priority_order": ["critical"],
  "test_cases": [{
    "id": "TC_BAD",
    "title": "bad",
    "type": "api",
    "priority": "critical",
    "test_data": {},
    "steps": [{"step": 1, "action": "PUT /cms/items/item_12345", "data": {}}],
    "expected": {"status_code": 400},
    "status": "untested"
  }],
  "risks": [],
  "assumptions": [],
  "estimated_duration_min": 5,
  "created_by": "QC-Agent-Planner"
}
"""


def test_planner_node_rejects_invented_ids(monkeypatch):
    import agents.planner as planner_mod

    monkeypatch.setattr(planner_mod, "create_planner_llm", lambda: _FakeLLM(BAD_PLAN))
    result = planner_node(_base_state())
    assert result.get("test_plan") is None
    assert result.get("error")
    assert "invented" in result["error"].lower() or "TC_BAD" in result["error"]
```

- [ ] **Step 2: Run test — expect FAIL** (planner currently accepts BAD_PLAN)

Run: `pytest tests/test_planner_id_policy.py::test_planner_node_rejects_invented_ids -v`

- [ ] **Step 3: Update `PLANNER_SYSTEM` and `planner_node`**

Add to `PLANNER_SYSTEM` (after existing rules list):

```text
8. **CẤM ID bịa** cho mutate (PUT/PATCH/DELETE): không viết `item_12345` hoặc UUID bịa vào path.
   Nguồn ID hợp lệ chỉ gồm:
   (a) `seed_ref` + `{{item_id}}` khi có Seed Manifest,
   (b) multi-step: POST tạo resource → `extract` → dùng `{{extracted_key}}`,
   (c) `test_data` do user/tài liệu cung cấp sẵn.
9. Ưu tiên path OpenAPI thật (ví dụ `/cms/items/{itemId}`), không bịa `/api/v1/...` nếu spec không có.
```

In `planner_node`, after building `codeintel_section`, add manifest section:

```python
    seed_manifest = state.get("seed_manifest")
    seed_section = ""
    if seed_manifest:
        seed_section = (
            "\nSeed Manifest (ID thật — dùng seed_ref / {{item_id}} / {{seed:alias}}):\n"
            f"{json.dumps(seed_manifest, ensure_ascii=False, indent=2)}\n"
        )
```

Append `{seed_section}` into `user_content` next to `{codeintel_section}`.

After successful `TestPlan.model_validate(plan_dict)`:

```python
    from agents.id_policy import validate_mutate_ids

    id_errors = validate_mutate_ids(plan_dict, seed_manifest=state.get("seed_manifest"))
    if id_errors:
        return {
            "test_plan": None,
            "error": "Planner ID policy failed:\n- " + "\n- ".join(id_errors),
            "current_step": "planner_failed",
            "messages": [response],
        }
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_planner_id_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/planner.py tests/test_planner_id_policy.py
git commit -m "feat(sandbox): enforce ID policy in planner_node"
```

---

### Task 4: Resolve `seed_ref` in API executor

**Files:**
- Modify: `agents/api_executor.py`
- Test: `tests/test_seed_ref_executor.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_seed_ref_executor.py
from agents.api_executor import _resolve_seed_refs


def test_resolve_seed_ref_sets_item_id():
    test = {
        "seed_ref": "item.cafe_kem_may",
        "test_data": {"quantity": 0},
        "steps": [{"action": "PUT /cms/items/{{item_id}}", "data": {"quantity": "{{quantity}}"}}],
    }
    manifest = {
        "entities": {"item.cafe_kem_may": "a078b105-7140-47da-bb79-7ba228808a6f"}
    }
    ctx = _resolve_seed_refs(test, manifest)
    assert ctx["item_id"] == "a078b105-7140-47da-bb79-7ba228808a6f"
    assert ctx["quantity"] == 0


def test_resolve_seed_templates_in_values():
    test = {
        "test_data": {"item_id": "{{seed:item.cafe_kem_may}}"},
        "steps": [],
    }
    manifest = {
        "entities": {"item.cafe_kem_may": "a078b105-7140-47da-bb79-7ba228808a6f"}
    }
    ctx = _resolve_seed_refs(test, manifest)
    assert ctx["item_id"] == "a078b105-7140-47da-bb79-7ba228808a6f"
```

- [ ] **Step 2: Run — expect FAIL** (`_resolve_seed_refs` missing)

- [ ] **Step 3: Implement helper and wire into `_run_single_api_test`**

Add to `agents/api_executor.py`:

```python
_SEED_TMPL = re.compile(r"^\{\{seed:([\w.\-]+)\}\}$")


def _resolve_seed_refs(test: dict, seed_manifest: dict | None) -> dict[str, Any]:
    context: dict[str, Any] = dict(test.get("test_data") or {})
    entities = (seed_manifest or {}).get("entities") or {}
    seed_ref = test.get("seed_ref")
    if seed_ref and seed_ref in entities:
        context.setdefault("item_id", entities[seed_ref])
    for k, v in list(context.items()):
        if isinstance(v, str):
            m = _SEED_TMPL.match(v.strip())
            if m and m.group(1) in entities:
                context[k] = entities[m.group(1)]
    return context
```

Change `_run_single_api_test` signature to accept optional manifest:

```python
def _run_single_api_test(test: dict, base_url: str, seed_manifest: dict | None = None) -> dict:
```

Replace:

```python
context: dict[str, Any] = dict(test.get("test_data") or {})
```

with:

```python
context: dict[str, Any] = _resolve_seed_refs(test, seed_manifest)
```

In `api_executor_node`, pass `state.get("seed_manifest")` into `_run_single_api_test`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_seed_ref_executor.py tests/test_planner_id_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/api_executor.py tests/test_seed_ref_executor.py
git commit -m "feat(sandbox): resolve seed_ref and seed templates in API executor"
```

---

### Task 5: Default `agent.yaml` + Phase 0 docs note

**Files:**
- Create: `agent.yaml`
- Modify: `docs/CODEINTEL_GUIDE.md` OR create short `docs/SANDBOX_GUIDE.md` (prefer new short doc)

- [ ] **Step 1: Create `agent.yaml`**

```yaml
sandbox_mode: external
db_type: postgres
schema: catalog
migrate_cmd: "npm run drizzle:migrate"
seed:
  strategy: sql
  paths:
    - "seed/*.sql"
manifest:
  emit: true
  aliases:
    item.cafe_kem_may: "a078b105-7140-47da-bb79-7ba228808a6f"
    item.combo_tinh_ca_ngay: "7b60dbd8-4cd0-4301-bcf9-b0d6b7508870"
health_path: /health
```

- [ ] **Step 2: Create `docs/SANDBOX_GUIDE.md`** with: Phase 0 usage (external), how seed_ref works, reminder that Docker sandbox is Phase 1+, link to design spec.

- [ ] **Step 3: Manual smoke (no Docker)**

```bash
ENABLE_CODEINTEL=true python main.py run \
  "Viết API test cập nhật item: bắt buộc create→extract hoặc seed_ref, path /cms/items/{id}, case qty<=0" \
  --code /Users/binhvc/Hpos/catalog-service \
  --openapi /Users/binhvc/Hpos/catalog-service/openapi.json
```

Expect: plan không còn `item_12345`; nếu LLM vẫn bịa → planner_failed với ID policy error.

- [ ] **Step 4: Commit**

```bash
git add agent.yaml docs/SANDBOX_GUIDE.md
git commit -m "docs(sandbox): add agent.yaml and Phase 0 sandbox guide"
```

**Checkpoint:** Phase 0 complete — shippable on current app without sandbox containers.

---

### Task 6: SandboxConfig loader

**Files:**
- Create: `sandbox/__init__.py`
- Create: `sandbox/config.py`
- Test: extend `tests/test_sandbox_config.py`

- [ ] **Step 1: Failing test**

```python
from sandbox.config import load_sandbox_config, SandboxConfig


def test_load_agent_yaml(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text(
        """
sandbox_mode: external
db_type: postgres
schema: catalog
migrate_cmd: "npm run drizzle:migrate"
seed:
  strategy: sql
  paths: ["seed/*.sql"]
manifest:
  emit: true
  aliases:
    item.cafe_kem_may: "a078b105-7140-47da-bb79-7ba228808a6f"
health_path: /health
""",
        encoding="utf-8",
    )
    cfg = load_sandbox_config(p)
    assert isinstance(cfg, SandboxConfig)
    assert cfg.sandbox_mode == "external"
    assert cfg.db_type == "postgres"
    assert cfg.manifest_aliases["item.cafe_kem_may"].startswith("a078b105")
```

- [ ] **Step 2: Implement**

```python
# sandbox/__init__.py
"""Session-scoped DB sandbox for QC Agent (Postgres-first)."""

# sandbox/config.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml


@dataclass
class SandboxConfig:
    sandbox_mode: str = "external"
    db_type: str = "postgres"
    schema: str = "catalog"
    migrate_cmd: str = ""
    seed_strategy: str = "sql"
    seed_paths: list[str] = field(default_factory=list)
    manifest_emit: bool = True
    manifest_aliases: dict[str, str] = field(default_factory=dict)
    health_path: str = "/health"
    app_start_cmd: str = ""
    app_cwd: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def load_sandbox_config(path: Path | str) -> SandboxConfig:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    seed = data.get("seed") or {}
    manifest = data.get("manifest") or {}
    return SandboxConfig(
        sandbox_mode=str(data.get("sandbox_mode", "external")),
        db_type=str(data.get("db_type", "postgres")),
        schema=str(data.get("schema", "public")),
        migrate_cmd=str(data.get("migrate_cmd", "")),
        seed_strategy=str(seed.get("strategy", "sql")),
        seed_paths=list(seed.get("paths") or []),
        manifest_emit=bool(manifest.get("emit", True)),
        manifest_aliases=dict(manifest.get("aliases") or {}),
        health_path=str(data.get("health_path", "/health")),
        app_start_cmd=str(data.get("app_start_cmd", "")),
        app_cwd=str(data.get("app_cwd", "")),
        raw=data,
    )
```

- [ ] **Step 3: pytest PASS + commit**

```bash
git add sandbox/__init__.py sandbox/config.py tests/test_sandbox_config.py
git commit -m "feat(sandbox): load SandboxConfig from agent.yaml"
```

---

### Task 7: Postgres provider (Docker Compose)

**Files:**
- Create: `environments/docker-compose.sandbox-pg.yml`
- Create: `sandbox/provider.py`
- Create: `sandbox/postgres.py`
- Test: `tests/test_sandbox_provider.py`
- Modify: `requirements.txt` — add `psycopg[binary]>=3.2.0`

Compose file:

```yaml
services:
  pg:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
      POSTGRES_DB: catalog_service
    ports:
      - "${SANDBOX_PG_PORT}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U test -d catalog_service"]
      interval: 2s
      timeout: 3s
      retries: 30
```

Provider behavior:
- `start()`: pick free host port; `docker compose -p qc_sandbox_<uuid> -f environments/docker-compose.sandbox-pg.yml up -d`; wait healthy; return env dict (`DB_HOST=localhost`, `DB_PORT=<port>`, `DB_USER=test`, `DB_PASSWORD=test`, `DB_NAME=catalog_service`, `DB_SCHEMA=catalog`, `DATABASE_URL=postgresql://test:test@localhost:<port>/catalog_service`).
- `reset()`: `psycopg` connect and `DO $$ ... TRUNCATE each table in schema CASCADE` (or `TRUNCATE ... CASCADE` for known tables). Set `supports_fast_reset = True`.
- `stop()`: `docker compose -p ... down -v`.

- [ ] **Step 1: Unit test factory without Docker**

```python
from sandbox.provider import create_provider
from sandbox.config import SandboxConfig


def test_create_provider_postgres():
    cfg = SandboxConfig(db_type="postgres")
    p = create_provider(cfg)
    assert p is not None
    assert p.supports_fast_reset is True


def test_create_provider_rejects_mongo():
    cfg = SandboxConfig(db_type="mongo")
    try:
        create_provider(cfg)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "postgres" in str(e).lower() or "unsupported" in str(e).lower()
```

- [ ] **Step 2: Integration test (skip if no Docker)**

```python
import os
import pytest
import shutil
from sandbox.config import SandboxConfig
from sandbox.postgres import PostgresSandboxProvider


docker = shutil.which("docker")

@pytest.mark.skipif(not docker, reason="Docker not available")
def test_postgres_provider_lifecycle():
    provider = PostgresSandboxProvider(schema="catalog")
    env = provider.start()
    assert env["DB_HOST"] == "localhost"
    assert env["DB_PORT"]
    assert "DATABASE_URL" in env
    # reset on empty schema should not raise hard (schema may not exist yet)
    try:
        provider.reset()
    except Exception:
        pass
    provider.stop()
```

- [ ] **Step 3: Implement Protocol + factory + PostgresSandboxProvider**

```python
# sandbox/provider.py
from __future__ import annotations
from typing import Protocol
from sandbox.config import SandboxConfig


class SandboxProvider(Protocol):
    supports_fast_reset: bool
    def start(self) -> dict[str, str]: ...
    def reset(self) -> None: ...
    def stop(self) -> None: ...


def create_provider(cfg: SandboxConfig) -> SandboxProvider:
    if cfg.db_type != "postgres":
        raise ValueError(f"Unsupported db_type for MVP: {cfg.db_type} (only postgres)")
    from sandbox.postgres import PostgresSandboxProvider
    return PostgresSandboxProvider(schema=cfg.schema)
```

Implement `sandbox/postgres.py` with subprocess docker compose + psycopg as described above. Store `project_name`, `port`, compose file path on the instance.

- [ ] **Step 4: Run unit tests always; integration if Docker present**

```bash
pytest tests/test_sandbox_provider.py -v
```

- [ ] **Step 5: Commit**

```bash
git add environments/docker-compose.sandbox-pg.yml sandbox/provider.py sandbox/postgres.py requirements.txt tests/test_sandbox_provider.py
git commit -m "feat(sandbox): Postgres Docker Compose provider with reset/stop"
```

---

### Task 8: Seed + manifest builder

**Files:**
- Create: `sandbox/seed.py`
- Test: `tests/test_seed_manifest.py`

- [ ] **Step 1: Failing tests (no Docker — pure manifest from aliases)**

```python
from sandbox.seed import build_manifest_from_aliases
from sandbox.config import SandboxConfig


def test_build_manifest_from_aliases():
    cfg = SandboxConfig(
        schema="catalog",
        manifest_aliases={
            "item.cafe_kem_may": "a078b105-7140-47da-bb79-7ba228808a6f",
        },
    )
    m = build_manifest_from_aliases(cfg, version_suffix="testhash")
    assert m["entities"]["item.cafe_kem_may"] == "a078b105-7140-47da-bb79-7ba228808a6f"
    assert m["version"].startswith("catalog@")
```

- [ ] **Step 2: Implement**

```python
# sandbox/seed.py
from __future__ import annotations
import hashlib
import subprocess
from pathlib import Path
from typing import Any
import psycopg
from sandbox.config import SandboxConfig


def build_manifest_from_aliases(cfg: SandboxConfig, version_suffix: str = "") -> dict[str, Any]:
    suffix = version_suffix or "static"
    return {
        "version": f"{cfg.schema}@{suffix}",
        "entities": dict(cfg.manifest_aliases),
    }


def apply_sql_seeds(database_url: str, project_root: Path, glob_patterns: list[str]) -> None:
    files: list[Path] = []
    for pattern in glob_patterns:
        files.extend(sorted(project_root.glob(pattern)))
    if not files:
        raise FileNotFoundError(f"No seed files for {glob_patterns} under {project_root}")
    with psycopg.connect(database_url) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS catalog")
        for f in files:
            sql = f.read_text(encoding="utf-8")
            conn.execute(sql)  # psycopg3 may need execute on connection with multiple statements — use conn.execute or cursor.execute; if multi-statement issues, use conn.execute with sql and autocommit
        conn.commit()


def run_migrate(cmd: str, cwd: Path, env: dict[str, str]) -> None:
    if not cmd:
        return
    merged = {**dict(**__import__("os").environ), **env}
    subprocess.run(cmd, shell=True, cwd=str(cwd), env=merged, check=True)


def hash_seed_files(project_root: Path, patterns: list[str]) -> str:
    h = hashlib.sha256()
    for pattern in patterns:
        for f in sorted(project_root.glob(pattern)):
            h.update(f.read_bytes())
    return h.hexdigest()[:12]
```

Note for implementer: if `conn.execute` rejects multi-statement SQL, use `psycopg.Connection` with `conn.execute` per statement split or `cursor.execute` in autocommit mode. Prefer applying each file via `psycopg.connect(..., autocommit=True)` and `cur.execute(sql)`.

- [ ] **Step 3: pytest + commit**

```bash
git add sandbox/seed.py tests/test_seed_manifest.py
git commit -m "feat(sandbox): build seed manifest from agent.yaml aliases"
```

---

### Task 9: Session context manager

**Files:**
- Create: `sandbox/session.py`
- Test: `tests/test_sandbox_provider.py` (add session test, Docker-gated)

```python
# sandbox/session.py
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from sandbox.config import SandboxConfig, load_sandbox_config
from sandbox.provider import create_provider
from sandbox.seed import (
    apply_sql_seeds,
    build_manifest_from_aliases,
    hash_seed_files,
    run_migrate,
)


@contextmanager
def sandbox_session(
    cfg: SandboxConfig,
    app_project_root: Path,
) -> Iterator[tuple[dict[str, str], dict]]:
    """Yields (db_env, seed_manifest). No-op-ish for external mode (empty env, alias manifest only)."""
    if cfg.sandbox_mode == "external":
        version = hash_seed_files(app_project_root, cfg.seed_paths) if cfg.seed_paths else "external"
        manifest = build_manifest_from_aliases(cfg, version_suffix=version) if cfg.manifest_emit else None
        yield {}, manifest or {"version": "external", "entities": {}}
        return

    provider = create_provider(cfg)
    env: dict[str, str] = {}
    try:
        env = provider.start()
        run_migrate(cfg.migrate_cmd, cwd=app_project_root, env=env)
        if cfg.seed_strategy == "sql":
            apply_sql_seeds(env["DATABASE_URL"], app_project_root, cfg.seed_paths)
        version = hash_seed_files(app_project_root, cfg.seed_paths)
        manifest = build_manifest_from_aliases(cfg, version_suffix=version)
        yield env, manifest
    finally:
        provider.stop()
```

- [ ] Test external mode yields alias manifest without Docker.
- [ ] Commit:

```bash
git add sandbox/session.py tests/test_sandbox_provider.py
git commit -m "feat(sandbox): session context manager with external and db_only modes"
```

---

### Task 10: Wire seed_manifest into `main.py` run path

**Files:**
- Modify: `main.py` (`run` command)
- Optionally load aliases even in external mode so Planner gets real catalog UUIDs

- [ ] **Step 1:** In `run()`, before building `initial_state`, load config:

```python
from sandbox.config import load_sandbox_config
from sandbox.session import sandbox_session
from pathlib import Path

cfg = load_sandbox_config(settings.agent_yaml_path)
app_root = Path((code or ['.'])[0]).resolve() if code else Path('.')
# For MVP external: use context manager only to get manifest
with sandbox_session(cfg, app_root) as (_env, seed_manifest):
    initial_state = { ..., "seed_manifest": seed_manifest }
    # existing stream loop using initial_state
```

Careful: today's `run()` streams the graph inside the function body — keep the entire graph invocation **inside** the `with` block so `db_only` containers stay up during execute. For `external`, `with` is cheap.

- [ ] **Step 2:** Manual: with default `agent.yaml`, run planner-only smoke and confirm seed manifest section appears (temporary debug print OK, remove before commit) OR unit-test that `initial_state` construction helper returns entities.

Extract a small helper if needed:

```python
# sandbox/bootstrap.py (optional)
def prepare_run_context(settings, code_paths: list[str]):
    ...
```

- [ ] **Step 3: Commit**

```bash
git add main.py sandbox/session.py
git commit -m "feat(sandbox): inject seed_manifest into AgentState on run"
```

**Checkpoint:** Phase 1+3 MVP — external uses static aliases from `agent.yaml`; `db_only` can start Postgres + seed when mode flipped.

---

### Task 11 (Phase 2): App orchestrator optional

**Files:**
- Create: `sandbox/app_orchestrator.py`
- Test: unit-test port allocation + health poll with `httpx` against a tiny `http.server` fixture; skip full Nest boot in CI.

Implement:
- `find_free_port()`
- `wait_for_health(base_url, path, timeout_s)`
- `AppProcess` context manager: subprocess `Popen(cfg.app_start_cmd, cwd=cfg.app_cwd, env={**os.environ, **db_env, "PORT": port})`, yield `http://127.0.0.1:{port}`, terminate on exit.

Wire into `sandbox_session` only when `cfg.sandbox_mode == "full_local"`.

Commit: `feat(sandbox): optional full_local app orchestrator`

---

### Task 12: CLI flag / settings for mode override

**Files:**
- Modify: `main.py` — add `--sandbox-mode` option overriding yaml
- Modify: `config/settings.py` if needed

```bash
python main.py run "..." --code /path/to/catalog-service --sandbox-mode db_only
```

Commit: `feat(sandbox): CLI override for sandbox_mode`

---

### Task 13 (Phase 5): TEMPLATE warm path (after metrics exist)

**Files:**
- Modify: `sandbox/postgres.py`

Add optional method `bake_template(template_name="app_seed")` and `reset_via_template()`:
1. After first migrate+seed, `CREATE DATABASE app_seed WITH TEMPLATE current` (force disconnect).
2. On reset: drop working DB / recreate `WITH TEMPLATE app_seed`.

Document cold vs warm timings in `docs/SANDBOX_GUIDE.md`. Do not claim warm SLA for cold starts.

Commit: `feat(sandbox): postgres TEMPLATE reset path`

---

## Spec coverage self-check

| Spec item | Task(s) |
|-----------|---------|
| Phase 0 ID policy / validator | 2, 3 |
| seed_ref + extract + user test_data | 2, 3, 4 |
| Prefer OpenAPI paths in prompt | 3 |
| AgentState.seed_manifest | 1, 10 |
| agent.yaml contract | 5, 6 |
| Postgres-only provider start/reset/stop | 7 |
| SQL seed + manifest | 8, 9 |
| Session CM / external default | 9, 10 |
| full_local orchestrator | 11 |
| Mode override | 12 |
| TEMPLATE / warm path | 13 |
| Independent from Codeintel | unchanged retriever; manifest parallel injection |
| No multi-DB MVP | Task 7 factory rejects non-postgres |

## Placeholder / consistency scan

- Types aligned: `SandboxConfig`, `SandboxProvider`, `seed_manifest` dict with `version` + `entities`.
- `validate_mutate_ids(plan, seed_manifest)` used by planner tests and node.
- `_resolve_seed_refs` used by executor.
- No TBD steps remaining for Phase 0–3 MVP.

---

## Manual PoC checklist (catalog-service)

1. Phase 0: `ENABLE_CODEINTEL=true` + openapi path; plan uses `/cms/items` + extract or seed_ref.
2. Put real UUIDs in `agent.yaml` aliases (already from seed SQL).
3. Flip `sandbox_mode: db_only` only after Task 10; point catalog `.env` to printed `DB_PORT` **or** use `full_local` after Task 11.
4. Confirm Chaos compose still separate (`docker-compose.chaos.yml`).
