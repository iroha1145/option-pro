# 隔离环境

记录时间：2026-09-13。本机**不是**生产 16 核 / 32 GiB 机器；下列数字是实测配额，配置文件没有、也不能模拟出不存在的算力。

## 机器

| 项 | 值 |
|----|----|
| OS | Ubuntu 24.04.4 LTS（`Linux cursor 6.12.94+` x86_64 KVM） |
| CPU | 4 核 Intel Xeon（online 0-3），无超线程 |
| 内存 | 15 GiB，无 swap |
| 磁盘 | `/dev/vdc` 254G，测量开始时约 247G 可用 |
| Docker | 无（Cloud Agent 无 daemon；与生产双容器不同） |
| 生产对照 | 美国 Linux 独立服务器 16 核 / 32 GiB / 512G SSD；本环境不能代表其容量或日本到美国 RTT |

## 运行时

| 项 | 值 |
|----|----|
| Python | 3.12.3（仓库 `.venv`） |
| Node | 仓库 `frontend-src/package.json` 锁定的 Vite 7 / Playwright 1.61.1 |
| 浏览器 | `/usr/local/bin/google-chrome`；Playwright Chromium（安装情况在测量日志记录） |
| WebKit / 真机 iPhone Safari | 未宣称已验证 |
| 后端启动 | 单进程 uvicorn，对齐 `backend/Dockerfile` CMD（无 `--workers`） |
| 前端 | 仓库 `frontend/` 生产构建产物，由 FastAPI SPA 静态托管；**不用** `vite dev` 冒充生产 |
| 数据根 | `$HOME/optix-perf-data/n{100,1000,10000}`；运行时 `DATA_DIR` 指向其一 |
| 访问模式 | `config/personal.toml` 的 `private_network`：本机回环视为 Owner |
| 外部上游 | 种子数据不打 MacroLens / OpenAI / 行情商；压测禁止打生产域名与付费供应商 |

## 对照工作区

```text
优化工作区  /workspace          分支 cursor/perf-sitewide-1d7a
未优化对照  $HOME/option-pro-unoptimized   31e8955d（detached）
```

## 启动（隔离）

```bash
mkdir -p "$HOME/optix-perf-data"
# 先种子，再启动，避免与写入争锁
DATA_DIR="$HOME/optix-perf-data/n10000" \
ALLOWED_HOSTS=localhost,127.0.0.1 \
HOST_BIND=127.0.0.1 \
PORT=2000 \
APP_COMMIT=31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc \
/workspace/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 2000 --app-dir /workspace/backend
```

健康：`curl -fsS http://127.0.0.1:2000/health` 与 `/ready`。

Worker 默认不启动。新闻读路径使用已 reconcile 的本地 `catalyst-cache.db`。需要后台任务回归时再单独拉起 `python -m app.worker`。

## 限速语义（实验室）

参考移动档与弱网档通过 Chrome DevTools Protocol 施加，**不是**日本到美国实测。

- 参考移动：390×844，CPU 4×，下载 10 Mbps，上传 2 Mbps，RTT 180 ms
- 弱网：390×844，CPU 6×，下载 1.6 Mbps，上传 0.75 Mbps，RTT 300 ms

只在浏览器侧施加一次，不再叠加操作系统 `tc`。不支持的模拟（真机、Safari、真实跨国链路）列为待验证。
