# Sharadar 接通与验收回传

实现 head：`02fa0a2ceb19f274ed81777a57936c1853c45a1f`。feature version 仍为 `us-eod-research-features-v1.6`，旧 B0 不按新定义重算。
旧 B0/R1/R1b/R2/Freeze/固定矩阵标为 `CONTROL_CURRENT_LIST_214`，不回写历史 JSON。

credential_present=false。
live_sharadar_request_count=0。
mock 红绿：14/14 满足（见 `sharadar_v3/connect_accept_probe_replay.json`）。本地全量 pytest：3990 passed, 6 skipped。
当前 head GitHub CI（`02fa0a2c`）：push https://github.com/iroha1145/option-pro/actions/runs/35419099251 与 PR https://github.com/iroha1145/option-pro/actions/runs/35419101191 均为 success。Python 测试与 pip audit 步骤均为 success。
未读取聊天凭据，未向 Nasdaq Data Link 或 Massive 试送密钥，未回退 Yahoo。
官方渠道为 `https://api.sharadar.com/v1.0/data/<table>`；跨域签名下载不再附 key。
分页先持久提交页再推进游标；max_pages/中断为 PARTIAL。HTTP 200 error/HTML 与 503 不能当 READ_OK。

四表状态：{"stocks": {"status": "AUTH_REQUIRED", "row_count": 0, "pages": 0, "complete": false, "session_row_count": 0}, "funds": {"status": "AUTH_REQUIRED", "row_count": 0, "pages": 0, "complete": false, "session_row_count": 0}, "tickers": {"status": "AUTH_REQUIRED", "row_count": 0, "pages": 0, "complete": false, "session_row_count": 0}, "actions": {"status": "AUTH_REQUIRED", "row_count": 0, "pages": 0, "complete": false, "session_row_count": 0}}。
16 个身份案例仍按实际 actions 验收；无行时为 fixture_list_only / AUTH_REQUIRED。
Yahoo 对账状态 `AUTH_REQUIRED`；volume scope `UNSUPPORTED`。
历史预算状态 `AUTH_REQUIRED`。

终态：`AUTH_REQUIRED`。这不是策略赢家状态。全市场选优未启动。
