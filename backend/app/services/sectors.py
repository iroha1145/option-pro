from __future__ import annotations

import asyncio
from statistics import mean
from typing import Any

from app.models.schemas import SectorHeatmapItem, SectorIVRank
from app.services.massive import MassiveClient

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


def atm_iv_from_chain(chain_results: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    if not chain_results:
        return None, None
    underlying = None
    candidates = []
    for item in chain_results:
        price = (item.get("underlying_asset") or {}).get("price")
        if price is not None:
            underlying = float(price)
        iv = item.get("implied_volatility")
        strike = (item.get("details") or {}).get("strike_price")
        if iv is not None and strike is not None:
            candidates.append((abs(float(strike) - float(price or underlying or strike)), float(iv)))
    if not candidates:
        return None, underlying
    candidates.sort(key=lambda x: x[0])
    # Average nearest few contracts so call/put quotes both contribute.
    nearest = [iv for _, iv in candidates[:4]]
    return round(mean(nearest), 4), underlying


def approximate_iv_percentile(iv: float | None) -> float | None:
    """MVP percentile approximation using a broad equity-options IV range.

    Maps IV in [10%, 80%] to percentile [0, 100]. This is a placeholder until
    historical IV storage is introduced.
    """
    if iv is None:
        return None
    pct = (iv - 0.10) / (0.80 - 0.10) * 100
    return round(max(0, min(100, pct)), 2)


def approximate_iv_change_30d(iv: float | None) -> float | None:
    if iv is None:
        return None
    # Deterministic lightweight proxy for MVP display; replace with stored history later.
    return round((iv * 0.07) - 0.01, 4)


async def sector_iv_ranking(sector_id: str, client: MassiveClient) -> list[SectorIVRank]:
    sector = SECTORS[sector_id]
    tickers: list[str] = sector["tickers"]

    async def one(ticker: str) -> dict[str, Any]:
        try:
            chain = await client.option_chain(ticker, limit=250)
            iv, price = atm_iv_from_chain(chain.get("results") or [])
            return {"ticker": ticker, "iv": iv, "price": price}
        except Exception:
            return {"ticker": ticker, "iv": None, "price": None}

    rows = await asyncio.gather(*(one(t) for t in tickers))
    rows.sort(key=lambda r: (-1 if r["iv"] is None else r["iv"]), reverse=True)
    return [
        SectorIVRank(
            ticker=row["ticker"],
            name=None,
            price=row["price"],
            iv_rank=i + 1,
            iv_pct=approximate_iv_percentile(row["iv"]),
            iv_change_30d=approximate_iv_change_30d(row["iv"]),
        )
        for i, row in enumerate(rows)
    ]


async def sector_heatmap(sector_id: str, client: MassiveClient) -> list[SectorHeatmapItem]:
    ranking = await sector_iv_ranking(sector_id, client)
    return [SectorHeatmapItem(ticker=item.ticker, iv_percentile=item.iv_pct) for item in ranking]
