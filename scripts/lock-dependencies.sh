#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [ -z "$UV_BIN" ]; then
    echo "uv is required to regenerate dependency locks: https://docs.astral.sh/uv/" >&2
    exit 1
fi

runtime_tmp="$(mktemp "${TMPDIR:-/tmp}/optix-runtime-lock.XXXXXX")"
ci_tmp="$(mktemp "${TMPDIR:-/tmp}/optix-ci-lock.XXXXXX")"
runtime_final="$(mktemp "${TMPDIR:-/tmp}/optix-runtime-lock-final.XXXXXX")"
ci_final="$(mktemp "${TMPDIR:-/tmp}/optix-ci-lock-final.XXXXXX")"
trap 'rm -f "$runtime_tmp" "$ci_tmp" "$runtime_final" "$ci_final"' EXIT

hash_files() {
    if command -v sha256sum >/dev/null 2>&1; then
        cat "$@" | sha256sum | awk '{print $1}'
    else
        cat "$@" | shasum -a 256 | awk '{print $1}'
    fi
}

common_args=(
    --generate-hashes
    --universal
    --python-version 3.12
    --resolution highest
    --upgrade
    --custom-compile-command "./scripts/lock-dependencies.sh"
    --quiet
)

"$UV_BIN" pip compile backend/requirements.in \
    --output-file "$runtime_tmp" \
    "${common_args[@]}"

"$UV_BIN" pip compile backend/requirements-ci.in \
    --output-file "$ci_tmp" \
    "${common_args[@]}"

runtime_source_hash="$(hash_files backend/requirements.in)"
ci_source_hash="$(hash_files backend/requirements.in backend/requirements-ci.in)"

{
    printf '# source-sha256: %s\n' "$runtime_source_hash"
    cat "$runtime_tmp"
} > "$runtime_final"
{
    printf '# source-sha256: %s\n' "$ci_source_hash"
    cat "$ci_tmp"
} > "$ci_final"

mv "$runtime_final" backend/requirements.txt
mv "$ci_final" backend/requirements-ci.txt
echo "Updated backend/requirements.txt and backend/requirements-ci.txt"
