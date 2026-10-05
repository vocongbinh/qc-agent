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
