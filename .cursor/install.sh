#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for Optix Pro.
#
# The formal product runs two containers (backend + worker) via Docker Compose,
# but Cloud Agent VMs have no Docker daemon. For development we run the same
# FastAPI app directly with uvicorn, which serves the prebuilt SPA in frontend/
# and the JSON API. This script only prepares durable, source-derived state:
# the Python virtualenv, hash-pinned dependencies, and the frontend toolchain.
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 1) Cursor's default image ships python3 but not the matching python3-venv on
#    Debian/Ubuntu, so `python3 -m venv` fails with "ensurepip is not available".
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    sudo apt-get update -qq
    sudo apt-get install -y "python${PYVER}-venv"
fi

# 2) Backend virtualenv with hash-pinned runtime + CI (pytest/pip-audit) deps.
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --require-hashes -r backend/requirements.txt
.venv/bin/python -m pip install --require-hashes -r backend/requirements-ci.txt

# 3) Frontend source dependencies (behavior tests + Vite build toolchain).
npm ci --prefix frontend-src --no-audit --no-fund

echo "Optix Pro environment ready."
