# 口令模式端到端服务器（自旧 frontend/visual-tests/support/password_server.py 移植）。
# 以 HTTPS + 自签名证书启动真实 Option Pro 后端，把访问模式临时改写为 password，
# 前端静态目录（FRONTEND_DIR）显式指向仓库 ../frontend —— 即 React SPA 的构建产物。
# SPA 无扩展名路径回退 index.html 与 /login 文档路径均由后端网关
# （backend/app/main.py 的 _SPAStaticFiles / _is_spa_document_path）提供，
# 本脚本无需再内嵌任何静态路由逻辑。
# 口令与端口环境变量保持原名：OPTIX_TEST_OWNER_PASSWORD / --port。
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


# frontend-src/visual-tests/support/password_server.py → parents[3] = 仓库根
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
# React SPA 构建产物（相对 frontend-src 即 ../frontend）
FRONTEND_DIR = REPOSITORY_ROOT / "frontend"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the real Option Pro app in password mode for browser tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    return parser.parse_args()


def _password_config(directory: Path) -> Path:
    # 复制 personal.toml 并把访问模式换成 password（只改一次，其他配置原样保留）
    source = REPOSITORY_ROOT / "config" / "personal.toml"
    target = directory / "personal-password.toml"
    content = source.read_text(encoding="utf-8")
    expected = 'mode = "private_network"'
    if expected not in content:
        raise RuntimeError("personal.toml no longer contains the private-network access mode")
    target.write_text(content.replace(expected, 'mode = "password"', 1), encoding="utf-8")
    return target


def _certificate(directory: Path) -> tuple[Path, Path]:
    # 一次性自签名证书：口令模式登录要求 HTTPS（后端 https_required 检查）
    certificate = directory / "certificate.pem"
    private_key = directory / "private-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return certificate, private_key


def main() -> None:
    args = _arguments()
    password = os.environ.pop("OPTIX_TEST_OWNER_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError("OPTIX_TEST_OWNER_PASSWORD must contain at least 12 characters")

    if not (FRONTEND_DIR / "index.html").is_file():
        raise RuntimeError(f"built SPA not found: {FRONTEND_DIR}/index.html")

    sys.path.insert(0, str(BACKEND_ROOT))
    with tempfile.TemporaryDirectory(prefix="optix-password-e2e-") as raw_directory:
        directory = Path(raw_directory)
        config_path = _password_config(directory)
        certificate, private_key = _certificate(directory)

        # 隔离开发者与部署机密，浏览器测试绝不触达真实外部服务。
        for variable in (
            "OPENAI_API_KEY",
            "FINNHUB_API_KEY",
            "INTERNAL_API_TOKEN",
            "APP_PASSWORD_HASH",
            "MASSIVE_API_KEY",
            "NEWSAPI_API_KEY",
            "GNEWS_API_KEY",
            "MARKETDATA_TOKEN",
            "MARKETDATA_API_TOKEN",
            "MACROLENS_URL",
        ):
            os.environ[variable] = ""
        os.environ["DATA_DIR"] = str(directory / "data")
        os.environ["HOST_BIND"] = args.host
        os.environ["ALLOWED_HOSTS"] = "localhost,127.0.0.1"
        os.environ["TRUST_PROXY_HEADERS"] = "false"
        # 必须在导入 app.main 之前设置：FRONTEND_DIR 在模块加载时读取。
        os.environ["FRONTEND_DIR"] = str(FRONTEND_DIR)

        from app import personal_config

        config = personal_config.load_personal_config(config_path)
        personal_config.get_personal_config = lambda: config

        from app.access import hash_owner_password

        os.environ["APP_PASSWORD_HASH"] = hash_owner_password(password)

        import uvicorn
        from app.main import app

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(private_key),
            log_level="warning",
            access_log=False,
        )


if __name__ == "__main__":
    main()
