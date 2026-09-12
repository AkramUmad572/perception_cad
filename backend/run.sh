#!/usr/bin/env bash
# Start Perception CAD backend (CadQuery) on :8000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate perception_cad
cd "$ROOT/backend"
export PYTHONPATH=.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
