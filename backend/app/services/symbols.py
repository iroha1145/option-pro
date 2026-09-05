"""Canonical quote symbols; display aliases never stand in for ETF prices."""

INDEX_ALIASES = {
    "SPX": "^GSPC", "GSPC": "^GSPC", "^SPX": "^GSPC",
    "NDX": "^NDX", "IXIC": "^IXIC", "DJI": "^DJI",
    "RUT": "^RUT", "VIX": "^VIX", "SOX": "^SOX",
    "N225": "^N225", "SSE": "000001.SS",
}


def quote_symbol(value: str) -> str:
    symbol = value.strip().upper()
    return INDEX_ALIASES.get(symbol, symbol)
