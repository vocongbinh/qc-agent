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
                    {"step": 1, "action": "PUT /cms/items/{{item_id}}", "data": {}}
                ],
                "expected": {"status_code": 200},
            }
        ]
    }
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
