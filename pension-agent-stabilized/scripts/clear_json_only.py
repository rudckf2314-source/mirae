from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = [
    ROOT / "data/cache/extracted",
    ROOT / "data/cache/parsed",
    ROOT / "data/standard_json",
]
INDEX = ROOT / "data/cache/index.json"

for directory in DIRS:
    directory.mkdir(parents=True, exist_ok=True)
    removed = sum(1 for path in directory.glob("*.json") if not path.unlink())
    print(f"cleared {directory.name}: {removed}")

INDEX.write_text('{"documents": []}', encoding="utf-8")
print("index reset")
print(
    "remaining",
    len(list((ROOT / "data/cache/extracted").glob("*.json"))),
    len(list((ROOT / "data/standard_json").glob("*.json"))),
)
