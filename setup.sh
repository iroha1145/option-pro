#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
umask 077

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

trap 'echo -e "${RED}安装在第 ${LINENO} 行失败，请查看上方信息。${NC}" >&2' ERR

set_file_value() {
    local file="$1"
    local key="$2"
    local value="$3"
    local temporary="${file}.tmp.$$"
    case "$value" in
        *$'\n'*|*$'\r'*)
            echo "配置值不能包含换行符。" >&2
            return 1
            ;;
    esac
    local encoded
    encoded="$(ENV_VALUE="$value" awk 'BEGIN {
        value=ENVIRON["ENV_VALUE"]
        gsub(/\047/, "\\\047", value)
        printf "\047%s\047", value
    }')"
    ENV_KEY="$key" ENV_VALUE="$encoded" awk '
        BEGIN { key=ENVIRON["ENV_KEY"]; value=ENVIRON["ENV_VALUE"]; found=0 }
        index($0, key "=") == 1 { print key "=" value; found=1; next }
        { print }
        END { if (!found) print key "=" value }
    ' "$file" > "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$file"
}

validate_service_secret() {
    local value="$1"
    if ! printf '%s' "$value" | python3 -c '
import sys

value = sys.stdin.read()
unsafe = "#\047\"\\$"
valid = (
    0 < len(value) <= 8192
    and all(33 <= ord(character) <= 126 for character in value)
    and not any(character in unsafe for character in value)
)
raise SystemExit(0 if valid else 1)
'; then
        echo "接口密钥包含不支持的字符。" >&2
        return 1
    fi
}

personal_access_mode() {
    awk '
        /^\[access\][[:space:]]*$/ { in_access=1; next }
        /^\[/ { in_access=0 }
        in_access && /^[[:space:]]*mode[[:space:]]*=/ {
            value=$0
            sub(/^[^=]*=[[:space:]]*/, "", value)
            sub(/[[:space:]]*#.*$/, "", value)
            gsub(/^[[:space:]\047\"]+|[[:space:]\047\"]+$/, "", value)
            print value
            exit
        }
    ' config/personal.toml
}

hash_owner_password() {
    printf '%s' "$1" | python3 -c '
import base64
import hashlib
import secrets
import sys

password = sys.stdin.read()
if len(password) < 12 or any(item in password for item in ("\0", "\r", "\n")):
    raise SystemExit("Owner password must contain at least 12 characters")
salt = secrets.token_bytes(16)
iterations = 600_000
digest = hashlib.pbkdf2_hmac(
    "sha256", password.encode("utf-8"), salt, iterations, dklen=32
)
encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
print(f"pbkdf2_sha256${iterations}${encode(salt)}${encode(digest)}")
'
}

migrate_legacy_machine_environment() {
    PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}" \
        python3 -m app.tools.migrate_legacy_machine_environment \
        .env secrets.env machine.env.example machine.env
}

require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo -e "${RED}未安装 Docker，请先安装 Docker Desktop。${NC}" >&2
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        echo -e "${RED}Docker 尚未运行。${NC}" >&2
        exit 1
    fi
    local version major minor
    version="$(docker compose version --short | sed 's/^v//')"
    IFS=. read -r major minor _ <<<"$version"
    if [ "${major:-0}" -lt 2 ] || {
        [ "${major:-0}" -eq 2 ] && [ "${minor:-0}" -lt 24 ];
    }; then
        echo "需要 Docker Compose 2.24 或更高版本，当前为 ${version}。" >&2
        exit 1
    fi
}

configure_environment() {
    local existing=false had_env=false had_machine=false had_secrets=false
    local migrated_legacy_machine=false
    [ ! -f .env ] || had_env=true
    [ ! -f machine.env ] || had_machine=true
    [ ! -f secrets.env ] || had_secrets=true
    if [ "$had_env" = true ] || [ "$had_machine" = true ] || [ "$had_secrets" = true ]; then
        existing=true
    fi
    if [ "$had_env" = false ]; then
        cp .env.example .env
    fi
    if [ "$had_machine" = false ]; then
        if [ "$had_env" = true ] || [ "$had_secrets" = true ]; then
            migrate_legacy_machine_environment
            migrated_legacy_machine=true
        else
            cp machine.env.example machine.env
        fi
    fi
    if [ "$had_secrets" = false ]; then
        cp secrets.env.example secrets.env
    fi
    chmod 600 .env machine.env secrets.env
    if [ "$existing" = true ]; then
        if [ "$migrated_legacy_machine" = true ]; then
            echo -e "${YELLOW}旧运行配置中的主机设置已迁移到 machine.env，缺失字段已使用模板默认值补齐。${NC}"
        else
            echo -e "${YELLOW}检测到已有运行配置；已有文件保持不变，缺失文件已按模板补齐。${NC}"
        fi
        return
    fi

    local openai_key macrolens_url macrolens_token
    local access_mode owner_password owner_password_confirm password_hash
    read -rsp "OpenAI 接口密钥（可留空）: " openai_key
    echo
    if [ -n "$openai_key" ]; then
        validate_service_secret "$openai_key"
    fi
    read -rp "MacroLens 地址（可留空）: " macrolens_url
    if [ -n "$macrolens_url" ]; then
        case "$macrolens_url" in
            https://*) ;;
            *)
                echo "MacroLens 地址必须使用 HTTPS。" >&2
                exit 1
                ;;
        esac
        read -rsp "MacroLens 内部令牌: " macrolens_token
        echo
        if [ -z "$macrolens_token" ]; then
            echo "填写 MacroLens 地址时也必须填写内部令牌。" >&2
            exit 1
        fi
        validate_service_secret "$macrolens_token"
    else
        macrolens_token=""
    fi

    access_mode="$(personal_access_mode)"
    if [ "$access_mode" = password ]; then
        read -rsp "Owner 密码（至少 12 个字符）: " owner_password
        echo
        read -rsp "再次输入 Owner 密码: " owner_password_confirm
        echo
        if [ "$owner_password" != "$owner_password_confirm" ]; then
            echo "两次输入的 Owner 密码不一致。" >&2
            exit 1
        fi
        password_hash="$(hash_owner_password "$owner_password")"
        unset owner_password owner_password_confirm
        set_file_value secrets.env APP_PASSWORD_HASH "$password_hash"
    elif [ "$access_mode" != private_network ]; then
        echo "config/personal.toml 中的访问模式无效。" >&2
        exit 1
    fi

    set_file_value secrets.env OPENAI_API_KEY "$openai_key"
    set_file_value secrets.env INTERNAL_API_TOKEN "$macrolens_token"
    set_file_value machine.env MACROLENS_URL "$macrolens_url"
    unset openai_key macrolens_token password_hash
    echo -e "${GREEN}.env、machine.env 与 secrets.env 已生成，文件权限为 0600。${NC}"
}

echo -e "${CYAN}${BOLD}Optix Pro 个人版安装${NC}"
require_docker
configure_environment

echo -e "${BOLD}构建并启动后台与统一工作进程……${NC}"
bash ./scripts/deploy.sh

published_address="$(docker compose port backend 8000 2>/dev/null || true)"
published_port="${published_address##*:}"
published_port="${published_port:-2000}"
echo -e "${GREEN}${BOLD}Optix Pro 已启动。${NC}"
if [ "$(personal_access_mode)" = password ]; then
    echo "密码模式已启用，请通过已配置的 HTTPS 反向代理访问。"
else
    echo -e "访问地址：${CYAN}http://localhost:${published_port}${NC}"
fi
echo "查看状态：docker compose ps"
echo "查看日志：docker compose logs -f backend worker"
echo "停止服务：docker compose down"
