from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pydantic import ValidationError

from app.access import OwnerAccessRuntime
from app.config import Settings
from app.data_paths import data_dir
from app.personal_config import load_personal_config
from app.runtime_environment import load_runtime_environment


def _safe_settings_error(exc: ValidationError) -> str:
    """Describe invalid settings without serializing their input values."""

    messages: list[str] = []
    for item in exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg", "invalid value"))
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        safe = f"{location}: {message}" if location else message
        if safe not in messages:
            messages.append(safe)
    return "; ".join(messages) or "runtime settings are invalid"


def _validated_settings() -> Settings:
    """Use the runtime Settings model as the single validation authority."""

    try:
        return Settings()
    except ValidationError as exc:
        raise ValueError(_safe_settings_error(exc)) from None


def _validate_container_data_dir() -> None:
    """Keep persistent writes inside the volume mounted by Compose."""

    try:
        root = data_dir()
    except ValueError:
        raise ValueError("DATA_DIR must be an absolute path under /data") from None
    volume_root = Path("/data")
    if root != volume_root and volume_root not in root.parents:
        raise ValueError("DATA_DIR must be /data or a directory under /data")


def validate(config_path: Path) -> dict[str, object]:
    load_runtime_environment()
    _validated_settings()
    _validate_container_data_dir()
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
