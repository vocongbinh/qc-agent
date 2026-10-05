import json
import sys
from pathlib import Path


def main():
    manifest_path = Path("/tmp/order_seed_manifest.json")
    for i, arg in enumerate(sys.argv):
        if arg == "--manifest" and i + 1 < len(sys.argv):
            manifest_path = Path(sys.argv[i + 1])

    data = {
        "version": 1,
        "SEEDED_PRODUCT_SKU": "SKU-TEST-001",
        "SEEDED_OUT_OF_STOCK_SKU": "SKU-OUT-OF-STOCK",
        "entities": {
            "default_user": "usr_99999",
        }
    }
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Seed complete. Manifest emitted to {manifest_path}")


if __name__ == "__main__":
    main()
