from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG_DIR = ROOT / "data" / "postgres"

if PG_DIR.exists():
    shutil.rmtree(PG_DIR)
    print(f"removed {PG_DIR}")
else:
    print("postgres dir absent")

PG_DIR.mkdir(parents=True, exist_ok=True)
print("ready for fresh embedded postgres init")
