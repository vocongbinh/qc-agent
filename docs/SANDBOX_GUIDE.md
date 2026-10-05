# Sandbox Runner & ID Policy Guide

## Phase 0 (available now)

QC Agent rejects invented mutate IDs (`item_12345`) in Planner output.

Valid ID sources for `PUT` / `PATCH` / `DELETE`:

1. `seed_ref` + Seed Manifest alias (from `agent.yaml`)
2. Multi-step create → `extract` → `{{var}}`
3. User-supplied values in `test_data`

### `seed_ref` example

```json
{
  "id": "TC_ITEM_UPDATE_001",
  "type": "api",
  "seed_ref": "item.cafe_kem_may",
  "test_data": { "quantity": 0 },
  "steps": [
    {
      "step": 1,
      "action": "PUT /cms/items/{{item_id}}",
      "data": { "quantity": 0 }
    }
  ],
  "expected": { "status_code": 400 }
}
```

Executor resolves `seed_ref` → concrete UUID into `item_id` before substitution.

### Config

See root `agent.yaml`. Default `sandbox_mode: external` (app already running at `DEFAULT_BASE_URL`).

## Later phases

- **Phase 1+3:** Postgres Docker sandbox + SQL seed + live `seed_manifest`
- **Phase 2:** optional `full_local` app subprocess
- **Phase 5:** TEMPLATE / warm reset

Design: `docs/superpowers/specs/2026-10-05-sandbox-runner-design.md`  
Plan: `docs/superpowers/plans/2026-10-05-sandbox-runner.md`

Sandbox is independent from Codeintel (`ENABLE_CODEINTEL`).

## Phase 5: Warm reset (TEMPLATE)

After migrate+seed once:

```python
provider.bake_template("app_seed")
# later between suites:
provider.reset_via_template("app_seed")
```

Benchmark **cold** (compose up + migrate + seed) separately from **warm** (TEMPLATE recreate).
Do not expect warm targets on a cold Docker pull.
