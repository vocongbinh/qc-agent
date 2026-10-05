# Multi-DB Sandbox Runner Design (Optimized for QC Agent)

**Date:** 2026-10-05  
**Status:** Draft for review  
**Target stack (PoC):** `catalog-service` (NestJS + Postgres/Drizzle + Redis)  
**Related:** Code Intelligence Graph (independent track)

---

## 1. Problem

QC Agent API executor is HTTP-only (`DEFAULT_BASE_URL` + `httpx`). It already supports multi-step, `{{var}}`, and response `extract`. Planner still invents IDs (`item_12345`), so mutate tests flake with 404 even when branch logic from Codeintel is correct.

Chaos compose (`environments/docker-compose.chaos.yml`) provides Redis/Postgres/Kafka/Toxiproxy for fault injection, but does **not** provide session-scoped seed/manifest or Planner ID contracts.

**Goals**

1. Stop LLM-invented resource IDs in mutate plans.
2. Provide optional Postgres sandbox with migrate + seed + `seed_manifest`.
3. Keep `external` mode (app already running) as default — do not require app subprocess for MVP.
4. Keep sandbox infra independent from Codeintel graph.

**Non-goals (MVP)**

- Multi-DB adapters (Mongo, etc.) in Phase 1
- Full app-as-subprocess as the only run mode
- Replacing Chaos lab or Codeintel
- Claiming sub-5s cold Docker+migrate+app boot

---

## 2. Recommended approach

**Approach C — ROI-first:**

1. **Phase 0 / P4:** Planner + validator policy (no fake IDs) — works on current app.
2. **Phase 1:** Single Postgres `SandboxProvider` (`start` / `reset` / `stop`).
3. **Phase 3:** Migrate + SQL seed + `seed_manifest` inject into Planner.
4. **Phase 2:** Optional app orchestrator (`full_local`); default remains `external` / `db_only`.
5. **Phase 5:** Warm-path performance (TRUNCATE, TEMPLATE, optional prebake).

Rejected for MVP:

- **A:** Full multi-DB + mandatory app subprocess first (slow ROI, wrong stack breadth).
- **B:** Policy-only forever (no isolation / reproducible seed).

---

## 3. Architecture

```text
sandbox_mode:
  external   → .env BASE_URL + existing app/DB (default)
  db_only    → start Postgres (+ Redis optional) + env keys; user runs app
  full_local → db_only + app subprocess + health poll (optional CI)

Session (not per-case docker):
  start provider
    → migrate once (single owner)
    → seed SQL
    → emit seed_manifest
    → AgentState.seed_manifest → Planner (+ Codeintel summary)
    → Executor HTTP (extract / {{var}} / seed_ref resolve)
    → reset (TRUNCATE CASCADE or TEMPLATE clone)
    → stop (context manager; always on exception)
```

### 3.1 Provider contract

```python
class SandboxProvider(Protocol):
    supports_fast_reset: bool

    def start(self) -> dict[str, str]:
        """Return standardized env keys for the app under test."""

    def reset(self) -> None:
        """Fast reset between suites/cases when supported."""

    def stop(self) -> None:
        """Tear down containers / resources."""
```

**Phase 1 adapter:** Postgres only.  
**Phase 1.b (optional):** Redis (`flushdb`), only when config requests it.

**Standard env keys** (map to catalog `.env.example`):

- `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_SCHEMA`
- `DATABASE_URL` (derived convenience)
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` (optional)

**Capabilities:** each adapter declares `supports_fast_reset`. Postgres MVP reset = `TRUNCATE … CASCADE` on configured schema (`catalog`). TEMPLATE clone is Phase 5/P3b.

**Prerequisite:** Docker available (socket or DinD in CI). Random compose project / DB name for parallel CI — no fixed global container name collision with chaos lab when both run.

### 3.2 Seed + manifest

**Strategy (catalog PoC):** SQL files under `catalog-service/seed/*.sql` (deterministic UUIDs already present).

**Manifest shape:**

```json
{
  "version": "catalog@<schema_or_seed_hash>",
  "entities": {
    "item.cafe_kem_may": "a078b105-7140-47da-bb79-7ba228808a6f",
    "item.combo_tinh_ca_ngay": "7b60dbd8-4cd0-4301-bcf9-b0d6b7508870"
  }
}
```

Aliases come from `agent.yaml` (SQL snippets or static maps over known seed IDs).

**Migrate ownership:** exactly one of runner **or** app runs migrate — never dual. Catalog PoC: runner invokes `npm run drizzle:migrate` (or equivalent) against sandbox DB when mode ≠ `external`.

**API-seed:** out of MVP unless SQL diverges from business rules; document as alternate `seed.strategy: api`.

### 3.3 Planner / Executor policy (Phase 0)

Valid ID sources for mutate paths (`PUT` / `PATCH` / `DELETE` / path params):

1. `seed_manifest` key / resolved UUID  
2. `extract` from a prior step in the same case  
3. User-supplied `test_data` (explicit in request/docs)

Otherwise plan is **invalid** (fail Planner validation, do not execute).

Prefer OpenAPI/codeintel paths (e.g. `/cms/items/{itemId}`), not invented `/api/v1/items/...`.

Executor changes: minimal — resolve `seed_ref` → concrete ID into step context before `_substitute`. Core multi-step/extract already exists in `agents/api_executor.py`.

Cleanup `DELETE` after case: optional; prefer session `reset()` end of suite.

### 3.4 App orchestrator (Phase 2, optional)

Only when `sandbox_mode: full_local`:

- Allocate free port
- Inject DB/Redis env from provider
- Start `app_start_cmd`
- Poll `health_path` until ready
- Context-manager teardown on success, exception, or kill

Fallback: if start fails, surface clear error; never silently fall back to production URLs.

### 3.5 Performance (Phase 5)

One optimization story:

1. Session reuse + schema `TRUNCATE`
2. Postgres `CREATE DATABASE … TEMPLATE app_seed` (or schema clone)
3. Optional tmpfs / pre-migrated image

Benchmark **cold** vs **warm** separately. Do not claim warm targets for cold path.

### 3.6 Relation to existing systems

| System | Role |
|--------|------|
| Codeintel | Scope/branches/deps for Planner — unchanged |
| Chaos compose | Fault injection — keep separate; may share Redis/PG images but different lifecycle |
| Sandbox runner | Isolation + real IDs for functional API runs |

---

## 4. `agent.yaml` contract

Per target project (PoC: catalog-service):

```yaml
sandbox_mode: external   # external | db_only | full_local
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
    # or SQL: "SELECT id FROM catalog.items WHERE name LIKE 'Cà Phê Kem Mây%' LIMIT 1"
health_path: /health
# full_local only:
# app_start_cmd: "npm run start:dev"
# app_cwd: "/path/to/catalog-service"
```

**Security**

- Manifest / admin seed credentials only for sandbox.
- When mode is `db_only` / `full_local`, forbid pointing mutate tests at production BASE_URL with production DB.
- No seed of production.

---

## 5. Repo layout (qc-agent)

```text
qc-agent/
  sandbox/
    __init__.py
    provider.py          # Protocol + factory
    postgres.py          # Phase 1
    redis.py             # Phase 1.b optional
    session.py           # context manager lifecycle
    seed.py              # migrate + seed + manifest
    app_orchestrator.py  # Phase 2 optional
  agent.yaml             # or targets/<app>/agent.yaml
  agents/
    state.py             # + seed_manifest
    planner.py           # policy + manifest injection
    api_executor.py      # seed_ref resolve
    graph.py             # optional session wrap / pre-node
  tests/
    test_sandbox_provider.py
    test_seed_manifest.py
    test_planner_id_policy.py
```

---

## 6. Phased deliverables & DoD

| Phase | Deliverable | Pass when |
|-------|-------------|-----------|
| **0 / P4** | Planner rules + plan validator + prompt for create→extract→mutate | Mutate plans have no invented IDs; CRUD extract flow works on live app/`external` |
| **1** | Postgres provider + session CM | `start → env → reset → stop` stable; cold start measured |
| **3** | Migrate + SQL seed + manifest → AgentState | `GET` with manifest item ID returns 200 on app using sandbox DB |
| **2** | Optional `full_local` orchestrator | If enabled: healthy app + clean teardown; default mode still `external` |
| **5** | Warm path (TRUNCATE / TEMPLATE / prebake) | Warm path meets internal target; cold path documented separately |

**Suggested calendar (1 engineer):** Phase 0 (1–2d) → Phase 1 (2–3d) → Phase 3 (3–4d) → Phase 2 optional → Phase 5.

---

## 7. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Scope creep multi-DB | Postgres-only until second app needs another engine |
| Dual migrate | Single owner in `agent.yaml` |
| App cannot run as subprocess | `external` / `db_only` modes |
| Seed SQL ≠ API rules | Document; switch `seed.strategy: api` later |
| Container name clash with chaos | Random project name / dedicated sandbox network |
| Manifest stale after schema change | `version` / hash; fail if migrate version mismatch |
| Planner ignores policy | Hard validator after LLM JSON, before human review |

---

## 8. Success metrics (PoC on catalog-service)

1. **Zero** invented path IDs in approved mutate plans for update-item flow.
2. At least one suite: seed_manifest ID → `PUT /cms/items/{id}` exercises qty/validation branches from Codeintel without 404 from missing row.
3. Sandbox session teardown leaves no orphan containers on exception.
4. `ENABLE_CODEINTEL=true` still works with/without sandbox (orthogonal).

---

## 9. Open decisions (locked for PoC unless changed)

1. First provider = **Postgres** (catalog Drizzle).  
2. Seed strategy MVP = **SQL** from `catalog-service/seed`.  
3. Default `sandbox_mode` = **external**.  
4. Phase order = **0 → 1 → 3 → 2 → 5**.  
5. Redis provider = optional P1.b, not blocking.  
6. TEMPLATE DB = optimization in P3b/P5, not MVP blocker.

---

## 10. One-line summary

**P4 policy + Postgres-only sandbox + SQL seed manifest** fix flaky fake IDs first; multi-DB and app subprocess are later upgrades, not MVP gates.
