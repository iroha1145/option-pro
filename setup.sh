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

set_env_value() {
    local key="$1"
    local value="$2"
    local temporary=".env.tmp.$$"
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
    ' .env > "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" .env
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
    if [ -f .env ]; then
        chmod 600 .env
        echo -e "${YELLOW}.env 已存在，本次沿用原配置。${NC}"
        return
    fi

    cp .env.example .env
    chmod 600 .env

    local openai_key macrolens_url macrolens_token auth_token
    read -rsp "OpenAI 接口密钥（可留空）: " openai_key
    echo
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
    else
        macrolens_token=""
    fi
    read -rsp "网页访问令牌（仅本机使用时可留空）: " auth_token
    echo

    set_env_value OPENAI_API_KEY "$openai_key"
    set_env_value MACROLENS_BASE_URL "$macrolens_url"
    set_env_value MACROLENS_INTERNAL_TOKEN "$macrolens_token"
    set_env_value APP_AUTH_TOKEN "$auth_token"
    echo -e "${GREEN}.env 已生成，文件权限为 0600。${NC}"
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
echo -e "访问地址：${CYAN}http://localhost:${published_port}${NC}"
echo "查看状态：docker compose ps"
echo "查看日志：docker compose logs -f backend worker"
echo "停止服务：docker compose down"
