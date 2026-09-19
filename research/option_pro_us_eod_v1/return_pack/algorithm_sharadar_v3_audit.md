# Sharadar 接通与验收回传

当前 head 由 git 记录。feature version 为 `us-eod-research-features-v1.6`，旧 B0 不按新定义重算。
旧 B0/R1/R1b/R2/Freeze/固定矩阵标为 `CONTROL_CURRENT_LIST_214`，不回写历史 JSON。

credential_present=false。
live_sharadar_request_count=0。
channel_confirmed=official_docs_mapped; live_channel_unconfirmed_without_secret。
未读取聊天凭据，未向 Nasdaq Data Link 或 Massive 试送密钥，未回退 Yahoo。
官方渠道为 `https://api.sharadar.com/v1.0/data/<table>`；跨域签名下载不再附 key。
下载模式：请求 `bulk_first`，实际 {}。
分页只在收到合法空页时才算完成；短页、空响应体、重定向、HTTP 错误都不算完成。中断后盘上已提交的行照常读取。

日期表分页请求区间固定为 from=2010-01-01 to=2024-06-28，sort=date.asc；主表不臆造日期参数。
探针是否允许全量下载：{"allowed": false, "blockers": ["credential_absent"], "empty_page_is_not_sample_proof": true}。
订阅档位探针：{"bulk_years": null, "status": "AUTH_REQUIRED", "attempts": []}。

原始下载状态 `AUTH_REQUIRED`；下载完成不等于验收通过。
分层状态：{"AUTH": "AUTH_REQUIRED", "TABLE_ACCESS": "AUTH_REQUIRED", "DOWNLOAD": "AUTH_REQUIRED", "TRANSFORM": "AUTH_REQUIRED", "IDENTITY": "AUTH_REQUIRED", "RECONCILE": "INSUFFICIENT", "HISTORY": "AUTH_REQUIRED", "VOLUME_SCOPE": "UNSUPPORTED", "EXECUTION": "UNSUPPORTED"}。
预登记必需项：["AUTH", "TABLE_ACCESS", "DOWNLOAD", "TRANSFORM", "IDENTITY", "RECONCILE", "HISTORY"]；accepted=false。

16 个身份案例：解析 0 个，具体终值 0 个；无行时为 fixture_list_only / AUTH_REQUIRED。
Yahoo 对账状态 `INSUFFICIENT`（no_comparable_rows）；对照源 {"kind": "captured_bar_cache", "available": true, "paths": [{"path": "/workspace/research/option_pro_us_eod_v1/data/cache/offline_replay/daily_bars.parquet", "rows": 10, "mapped_rows": 0}, {"path": "/workspace/research/option_pro_us_eod_v1/data/cache/round3_yahoo_abcd/bars.pkl", "rows": 449597, "mapped_rows": 0}], "identity_alignment": "cache_ticker_to_sharadar_permaticker", "unmapped_cache_ids": ["AAL", "AAPL", "ABBV", "ABT", "ADBE", "AEP", "AFRM", "ALB", "ALK", "AMAT", "AMD", "AMGN", "AMT", "AMX", "AMZN", "ANET", "ARKK", "ARM", "ASML", "AVGO", "AXP", "BA", "BABA", "BAC", "BIDU", "BIIB", "BILI", "BLNK", "BMY", "BNTX", "BTBT", "C", "CAT", "CCI", "CEG", "CFRUY", "CHPT", "CHTR", "CIFR", "CLSK", "CMCSA", "COIN", "COP", "COST", "CPA", "CRM", "CRWD", "CRWV", "CVX", "D", "DAL", "DDOG", "DE", "DELL", "DHR", "DIA", "DIS", "DLR", "DUK", "DVN", "EL", "EMR", "ENPH", "EOG", "EQIX", "ETN", "EXC", "F", "FDX", "FSLR", "FUBO", "GD", "GE", "GILD", "GLD", "GM", "GOOGL", "GS", "HD", "HEI", "HON", "HOOD", "HPQ", "HUT", "INTC", "IQ", "ISRG", "ITW", "IWM", "JBLU", "JD", "JNJ", "JPM", "KLAC", "LCID", "LHX", "LI", "LLY", "LMT", "LOGI", "LOW", "LRCX", "LUV", "LVMUY", "MA", "MARA", "MCD", "MDB", "MDT", "META", "MMM", "MPC", "MRK", "MRNA", "MRVL", "MS", "MSFT", "MSTR", "MU", "NEE", "NET", "NFLX", "NIO", "NKE", "NOC", "NOW", "NTES", "NVDA", "NVO", "O", "ORCL", "OXY", "PANW", "PCG", "PDD", "PFE", "PINS", "PLD", "PLTR", "PLUG", "PNC", "PSA", "PSKY", "PSX", "PVH", "PYPL", "QCOM", "QQQ", "RDDT", "REGN", "RIOT", "RIVN", "RL", "RMS.PA", "ROKU", "RTX", "RUN", "RYAAY", "SBUX", "SCHW", "SLB", "SMCI", "SNAP", "SNOW", "SO", "SOFI", "SONY", "SOXX", "SPG", "SPOT", "SPY", "SRE", "STLA", "T", "TAL", "TDG", "TFC", "TGT", "TJX", "TKO", "TLT", "TM", "TME", "TMO", "TMUS", "TPR", "TSLA", "TSM", "TXN", "TXT", "UAL", "UNH", "UPS", "UPST", "USB", "V", "VICI", "VLO", "VOD", "VOO"], "unmapped_n": 214, "rows_outside_allowed_window": 0, "return_basis": "total_return_index_tri_vs_sharadar_closeadj", "volume_basis": "tape_volume_from_split_volume_and_raw_close_ratio", "yahoo_is_not_truth": true, "live_yahoo_request": false}。
volume scope `UNSUPPORTED`。
历史预算状态 `AUTH_REQUIRED`；覆盖不等于授权，权限状态 `AUTH_REQUIRED`，authorized_range_unknown=true。
转换跳过行 0 条（占比 None），按原因计数，样本最多 1000 条。
策略池摘要：{"computed": false, "session_n": 0, "pool_size_median": null, "venue_unverified_share": null}。
公司行动词表（观察到的前 20 个）：{}。

终态：`AUTH_REQUIRED`。这不是策略赢家状态。全市场选优未启动。
