"""环境里的 MASSIVE_/SHARADAR_ 不能进入测试进程。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE = "OPTIX_SECRET_ISOLATION_PROBE"


def test_ambient_provider_secrets_are_stripped_before_settings() -> None:
    if os.environ.get(PROBE) == "1":
        from app.config import get_settings

        get_settings.cache_clear()
        assert "MASSIVE_API_KEY" not in os.environ
        assert "SHARADAR_API_KEY" not in os.environ
        assert "MASSIVE_BASE_URL" not in os.environ
        settings = get_settings()
        assert settings.massive_api_key == ""
        assert settings.massive_base_url == "https://api.massive.com"
        return

    env = os.environ.copy()
    env.update({
        "MASSIVE_API_KEY": "ambient-massive-key",
        "SHARADAR_API_KEY": "ambient-sharadar-key",
        "MASSIVE_BASE_URL": "https://ambient.invalid",
        "PYTHONPATH": "backend",
        PROBE: "1",
    })
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        str(Path(__file__).resolve()),
        "-k",
        "stripped_before_settings",
    ]
    leaked = subprocess.run(
        [*command, "--noconftest"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    leaked_output = leaked.stdout + leaked.stderr
    assert leaked.returncode != 0, leaked_output
    assert "AssertionError" in leaked_output

    isolated = subprocess.run(
        command,
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert isolated.returncode == 0, isolated.stdout + isolated.stderr
