#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
umask 077

# ─────────────────────────────────────────────
#  Optix Pro — 一键安装脚本
#  美股期权可视化平台 + AI 分析
# ─────────────────────────────────────────────

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

trap 'echo -e "${RED}✗ 安装在第 ${LINENO} 行失败。请查看上方错误信息。${NC}" >&2' ERR

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
    # Compose treats single-quoted dotenv values literally. Escape only the
    # quote delimiter so $, #, spaces and backslashes survive unchanged.
    local encoded
    encoded="$(ENV_VALUE="$value" awk 'BEGIN {
        value=ENVIRON["ENV_VALUE"]
        gsub(/\047/, "\\\047", value)
        printf "\047%s\047", value
    }')"
    ENV_KEY="$key" ENV_VALUE="$encoded" awk '
        BEGIN { key=ENVIRON["ENV_KEY"]; value=ENVIRON["ENV_VALUE"]; found=0 }
        index($0, key "=") == 1 {
            print key "=" value
            found=1
            next
        }
        { print }
        END { if (!found) print key "=" value }
    ' .env > "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" .env
}

echo -e "${CYAN}${BOLD}"
echo "  ┌─────────────────────────────────────┐"
echo "  │        Optix Pro Setup              │"
echo "  │   美股期权可视化 + AI 分析平台       │"
echo "  └─────────────────────────────────────┘"
echo -e "${NC}"

# ── 1. Check Docker ──
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker 未安装。请先安装 Docker Desktop: https://docker.com${NC}"
    exit 1
fi
if ! docker info &> /dev/null 2>&1; then
    echo -e "${RED}✗ Docker 未运行。请先启动 Docker Desktop${NC}"
    exit 1
fi
compose_version="$(docker compose version --short | sed 's/^v//')"
IFS=. read -r compose_major compose_minor _compose_patch <<<"$compose_version"
if [ "${compose_major:-0}" -lt 2 ] || {
    [ "${compose_major:-0}" -eq 2 ] && [ "${compose_minor:-0}" -lt 24 ];
}; then
    echo "Docker Compose 2.24 or newer is required; found ${compose_version}." >&2
    exit 1
fi
echo -e "${GREEN}✓${NC} Docker 已就绪"

# ── 2. Configure API Keys ──
if [ -f .env ]; then
    chmod 600 .env
    echo -e "${YELLOW}! .env 文件已存在，跳过配置${NC}"
    echo "  如需重新配置，请删除 .env 后重新运行"
    if grep -Eq '^OPENAI_BASE_URL=.+$' .env \
        && ! grep -Eiq '^ALLOW_CUSTOM_OPENAI_BASE_URL=(true|1|yes)$' .env; then
        echo -e "${RED}✗ 检测到旧版自定义 OPENAI_BASE_URL。${NC}" >&2
        echo "  确认该地址可信且使用代理专属 Key 后，请在 .env 添加：" >&2
        echo "  ALLOW_CUSTOM_OPENAI_BASE_URL=true" >&2
        exit 1
    fi
else
    echo ""
    echo -e "${BOLD}配置 OpenAI Responses API${NC}"
    echo -e "  用于 AI 异动分析和财报关联分析"
    echo ""

    # API Base URL. Blank means the official OpenAI endpoint.
    read -rp "  API Base URL [留空使用 OpenAI 官方地址]: " api_base
    allow_custom_base=false
    if [ -n "$api_base" ]; then
        echo -e "${YELLOW}! 使用第三方代理时，只能填写该代理签发的专属 Key。不要把 OpenAI 官方 Key 交给代理。${NC}"
        case "$api_base" in
            https://*|http://localhost|http://localhost/*|http://localhost:*|http://127.0.0.1|http://127.0.0.1/*|http://127.0.0.1:*) ;;
            *)
                echo -e "${RED}✗ 自定义公网 API Base URL 必须使用 HTTPS${NC}" >&2
                exit 1
                ;;
        esac
        read -rp "  确认你信任该地址且将使用代理专属 Key？[y/N]: " confirm_proxy
        case "$confirm_proxy" in
            y|Y|yes|YES) allow_custom_base=true ;;
            *)
                echo -e "${RED}✗ 已取消自定义 API 地址配置${NC}" >&2
                exit 1
                ;;
        esac
    fi

    # API Key
    read -rsp "  API Key（可留空禁用 AI）: " api_key
    echo ""
    if [ -z "$api_key" ]; then
        echo -e "${YELLOW}! 未提供 API Key，AI 分析功能将不可用${NC}"
    fi

    # Model
    read -rp "  模型 [gpt-5.4-mini-2026-03-17]: " model
    model=${model:-gpt-5.4-mini-2026-03-17}

    # Reasoning effort
    read -rp "  推理等级 (minimal/low/medium/high/xhigh) [low]: " reasoning
    reasoning=${reasoning:-low}
    case "$reasoning" in
        minimal|low|medium|high|xhigh) ;;
        *)
            echo -e "${YELLOW}! 无效推理等级，改用 low${NC}"
            reasoning=low
            ;;
    esac

    # Start from the complete, deployable template, then update only the
    # values entered above. Values are written as data and are never sourced
    # or evaluated as shell code.
    cp .env.example .env
    chmod 600 .env
    set_env_value OPENAI_API_KEY "$api_key"
    set_env_value OPENAI_BASE_URL "$api_base"
    set_env_value ALLOW_CUSTOM_OPENAI_BASE_URL "$allow_custom_base"
    set_env_value OPENAI_MODEL "$model"
    set_env_value OPENAI_REASONING "$reasoning"
    set_env_value FOCUS_PRODUCER_ENABLED false
    set_env_value FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS 120

    echo -e "${GREEN}✓${NC} .env 已生成（已加入 .gitignore，不会泄露）"
    echo -e "  默认仅监听 127.0.0.1；远程使用请优先通过 SSH 隧道或 VPN。"
fi

# ── 3. Build & Start ──
echo ""
echo -e "${BOLD}按当前 Git 提交构建并重建服务...${NC}"
if ! ./scripts/deploy.sh; then
    echo -e "${RED}✗ 部署未能通过构建、健康或版本检查${NC}" >&2
    exit 1
fi

echo ""
echo -e "${GREEN}${BOLD}✓ Optix Pro 启动成功！${NC}"
echo ""
published_address="$(docker compose port backend 8000 2>/dev/null || true)"
published_port="${published_address##*:}"
published_port="${published_port:-2000}"
echo -e "  ${CYAN}${BOLD}打开浏览器访问: http://localhost:${published_port}${NC}"
echo ""
echo -e "  API 文档: http://localhost:${published_port}/docs"
echo ""
echo -e "  ${BOLD}功能:${NC}"
echo "    • 市场总览 — 预设热门标的按板块分组"
echo "    • 个股详情 — K线图 + EMA/SMA + 成交量"
echo "    • 期权链 — IV/持仓量/异动检测"
echo "    • 异动扫描 — 10 只热门标的近月延迟数据"
echo "    • 顶部/底部信号 — 多维程序化评分"
echo "    • AI 分析 — 可选的 GPT 分析（部分财报功能联网）"
echo "    • 板块 IV — 波动率排名 + 热力图"
echo "    • 财报中心 — 69 家预设公司的 Yahoo 财报日历"
echo ""
echo -e "  ${BOLD}管理:${NC}"
echo "    停止: docker compose down"
echo "    重启: docker compose restart"
echo "    日志: docker compose logs -f"
echo ""
