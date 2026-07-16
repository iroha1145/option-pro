from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.access import OwnerAccessRuntime
from app.personal_config import load_personal_config
from app.runtime_environment import load_runtime_environment


def validate(config_path: Path) -> dict[str, object]:
    load_runtime_environment()
    config = load_personal_config(config_path)
    runtime = OwnerAccessRuntime(
        config.access,
        password_hash=os.environ.get("APP_PASSWORD_HASH", ""),
    )
    boundary = runtime.validate_startup(
        os.environ.get("HOST_BIND", "127.0.0.1"),
        allowed_hosts=os.environ.get("ALLOWED_HOSTS", ""),
        trust_proxy_headers=os.environ.get("TRUST_PROXY_HEADERS", "false"),
        trusted_proxy_cidrs=os.environ.get("TRUSTED_PROXY_CIDRS", ""),
    )
    return {
        "valid": True,
        "access_mode": boundary.access_mode,
        "host_bind": boundary.host_bind,
        "allowed_host_count": len(boundary.allowed_hosts),
        "trusted_proxy_cidr_count": len(boundary.trusted_proxy_cidrs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Personal Edition deployment boundary."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "config" / "personal.toml",
    )
    arguments = parser.parse_args(argv)
    try:
        report = validate(arguments.config)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"valid": False, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
