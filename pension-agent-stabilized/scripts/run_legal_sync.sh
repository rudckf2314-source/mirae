#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/sync_legal_db.py --fail-on-partial
