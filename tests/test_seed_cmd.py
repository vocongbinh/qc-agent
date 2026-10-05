import json
import sys
from pathlib import Path

from sandbox.seed import load_seed_manifest_file, run_seed_cmd


def test_load_seed_manifest_file(tmp_path: Path):
    manifest_file = tmp_path / "seed_manifest.json"
    manifest_file.write_text(
        json.dumps({
            "version": 1,
            "SEEDED_USER_ID": "usr_123",
            "SEEDED_PRODUCT_SKU": "SKU-999",
            "entities": {
                "order_id": "ord_555"
            }
        }),
        encoding="utf-8"
    )

    data = load_seed_manifest_file(manifest_file)
    assert data["version"] == 1
    assert data["entities"]["SEEDED_USER_ID"] == "usr_123"
    assert data["entities"]["SEEDED_PRODUCT_SKU"] == "SKU-999"
    assert data["entities"]["order_id"] == "ord_555"


def test_run_seed_cmd(tmp_path: Path):
    manifest_file = tmp_path / "out_manifest.json"
    script = tmp_path / "seed.py"
    script.write_text(
        f"""
import json, sys
data = {{"SEEDED_USER_ID": "usr_from_cmd", "version": 2}}
with open("{manifest_file}", "w") as f:
    json.dump(data, f)
""",
        encoding="utf-8"
    )

    cmd = f"{sys.executable} {script} --manifest {manifest_file}"
    result = run_seed_cmd(cmd, cwd=tmp_path, env={})
    assert result["entities"]["SEEDED_USER_ID"] == "usr_from_cmd"
    assert result["version"] == 2
