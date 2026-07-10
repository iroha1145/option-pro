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

    # Write .env (never committed to git)
    cat > .env << EOF
OPENAI_API_KEY=${api_key}
OPENAI_BASE_URL=${api_base}
ALLOW_CUSTOM_OPENAI_BASE_URL=${allow_custom_base}
OPENAI_MODEL=${model}
OPENAI_REASONING=${reasoning}
OPENAI_TIMEOUT_SECONDS=45
OPENAI_MAX_RETRIES=0
OPENAI_MAX_OUTPUT_TOKENS=1200
OPENAI_MAX_CONCURRENCY=2
HOST_BIND=127.0.0.1
PORT=2000
APP_AUTH_TOKEN=
TRUST_PROXY_HEADERS=false
ALLOWED_ORIGINS=
EOF

    chmod 600 .env

    echo -e "${GREEN}✓${NC} .env 已生成（已加入 .gitignore，不会泄露）"
    echo -e "  默认仅监听 127.0.0.1；远程使用请优先通过 SSH 隧道或 VPN。"
fi

# ── 3. Build & Start ──
echo ""
echo -e "${BOLD}构建 Docker 镜像...${NC}"
if ! docker compose build; then
    echo -e "${RED}✗ 镜像构建失败${NC}" >&2
    exit 1
fi

echo -e "${BOLD}启动服务...${NC}"
if ! docker compose up -d --wait --wait-timeout 180; then
    echo -e "${RED}✗ 服务未能通过健康检查，最近日志如下：${NC}" >&2
    docker compose ps >&2 || true
    docker compose logs --tail=200 backend >&2 || true
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
