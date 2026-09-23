#!/usr/bin/env bash

release_operation_lock() {
    if [ "$operation_lock_owned" = true ]; then
        rmdir "$operation_lock_dir" 2>/dev/null || true
        operation_lock_owned=false
    fi
}

acquire_operation_lock() {
    if ! mkdir -m 700 "$operation_lock_dir" 2>/dev/null; then
        echo "Another deployment or Personal command is running. If none is active, remove the stale .personal-operation.lock directory." >&2
        return 1
    fi
    operation_lock_owned=true
}
