from __future__ import annotations

from typing import Any

SECTORS: dict[str, dict[str, Any]] = {
    "semiconductors":       {"name": "半导体",     "tickers": ["NVDA", "AMD", "TSM", "AVGO", "ASML", "MU", "INTC", "ARM", "QCOM", "MRVL", "TXN", "LRCX", "KLAC", "AMAT"]},
    "software":             {"name": "软件基础设施", "tickers": ["MSFT", "ORCL", "CRM", "ADBE", "NOW", "SNOW", "PLTR", "PANW", "NET", "CRWD", "DDOG", "MDB"]},
    "ai_cloud":             {"name": "AI 与云",    "tickers": ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "PLTR", "SMCI", "ANET", "DELL", "CRWV"]},
    "biotech":              {"name": "生物技术",   "tickers": ["LLY", "NVO", "ABBV", "AMGN", "GILD", "VRTX", "REGN", "BIIB", "MRNA", "BNTX"]},
    "healthcare":           {"name": "医疗保健",   "tickers": ["UNH", "JNJ", "PFE", "MRK", "TMO", "ABT", "DHR", "ISRG", "MDT", "BMY"]},
    "consumer_electronics": {"name": "消费电子",   "tickers": ["AAPL", "SONY", "DELL", "HPQ", "LOGI"]},
    "automotive":           {"name": "汽车 / EV", "tickers": ["TSLA", "RIVN", "F", "GM", "LCID", "NIO", "LI", "XPEV", "STLA", "TM"]},
    "ev_supply":            {"name": "电动车供应链", "tickers": ["TSLA", "LCID", "RIVN", "ALB", "PLUG", "BLNK", "CHPT", "ENPH", "FSLR", "RUN"]},
    "finance":              {"name": "大型银行",   "tickers": ["JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "SCHW"]},
    "fintech":              {"name": "金融科技",   "tickers": ["V", "MA", "PYPL", "XYZ", "AXP", "COIN", "HOOD", "SOFI", "AFRM", "UPST"]},
    "retail":               {"name": "零售消费",   "tickers": ["AMZN", "WMT", "COST", "HD", "TGT", "LOW", "NKE", "SBUX", "MCD", "TJX"]},
    "luxury":               {"name": "奢侈品",     "tickers": ["LVMUY", "RMS.PA", "CFRUY", "EL", "TPR", "RL", "PVH"]},
    "media_streaming":      {"name": "媒体与流媒体", "tickers": ["NFLX", "DIS", "WBD", "PSKY", "ROKU", "SPOT", "FUBO", "TKO"]},
    "social_internet":      {"name": "社交与互联网", "tickers": ["META", "GOOGL", "SNAP", "PINS", "RDDT", "BIDU", "BABA", "PDD"]},
    "energy":               {"name": "能源",       "tickers": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "VLO", "PSX", "OXY", "DVN"]},
    "utilities":            {"name": "电力公用",   "tickers": ["NEE", "DUK", "SO", "AEP", "EXC", "SRE", "D", "PCG", "VST", "CEG"]},
    "defense_aero":         {"name": "国防航空",   "tickers": ["LMT", "RTX", "NOC", "GD", "BA", "LHX", "TDG", "HEI", "TXT"]},
    "airlines":             {"name": "航空运输",   "tickers": ["DAL", "UAL", "AAL", "LUV", "ALK", "JBLU", "RYAAY", "CPA"]},
    "real_estate":          {"name": "房地产",     "tickers": ["PLD", "AMT", "EQIX", "CCI", "SPG", "O", "PSA", "DLR", "WELL", "VICI"]},
    "crypto":               {"name": "加密相关",   "tickers": ["COIN", "MARA", "RIOT", "MSTR", "HUT", "CLSK", "WULF", "CIFR", "BTBT"]},
    "china_adr":            {"name": "中概 ADR",  "tickers": ["BABA", "PDD", "JD", "BIDU", "NIO", "LI", "XPEV", "NTES", "TME", "TAL", "BILI", "IQ"]},
    "telecom":              {"name": "电信",       "tickers": ["T", "VZ", "TMUS", "CHTR", "CMCSA", "VOD", "AMX"]},
    "industrials":          {"name": "工业制造",   "tickers": ["CAT", "DE", "HON", "GE", "MMM", "UPS", "FDX", "EMR", "ETN", "ITW"]},
    "etfs":                 {"name": "宽基 ETF",  "tickers": ["SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "ARKK", "SOXX", "XLF", "XLE", "GLD", "TLT"]},
}
