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
