"""Download daily OHLCV (unadjusted + adjusted close) for the universe with yfinance, in batches, to parquet."""
import sys, time, json
from pathlib import Path
import pandas as pd, yfinance as yf, warnings
warnings.filterwarnings("ignore")
root = Path(sys.argv[1]); start = sys.argv[2]; end = sys.argv[3]
tickers = [t.split(":")[0] for t in (root / "universe.txt").read_text().split()]
extra = ["SPY", "QQQ", "IWM", "RSP"]
todo = [t for t in tickers if t not in extra] + extra
out = root / "raw"; out.mkdir(exist_ok=True)
done = {p.stem for p in out.glob("batch_*.parquet")}
B = 150
log = open(root / "download.log", "a")
for i in range(0, len(todo), B):
    name = f"batch_{i//B:03d}"
    if name in done: continue
    batch = todo[i:i+B]
    for attempt in range(3):
        try:
            df = yf.download(batch, start=start, end=end, auto_adjust=False, actions=False, progress=False, group_by="ticker", threads=True)
            break
        except Exception as e:
            print(name, "retry", attempt, repr(e)[:120], file=log, flush=True); time.sleep(5)
    else:
        print(name, "FAILED", file=log, flush=True); continue
    frames = []
    for t in batch:
        try:
            d = df[t].dropna(subset=["Close"])
        except Exception:
            continue
        if d.empty: continue
        d = d[["Open","High","Low","Close","Adj Close","Volume"]].copy(); d.columns = ["open","high","low","close","adj_close","volume"]
        d["ticker"] = t; d.index.name = "date"; frames.append(d.reset_index())
    if frames:
        pd.concat(frames).to_parquet(out / f"{name}.parquet", index=False)
    print(name, len(batch), "tickers", sum(len(f) for f in frames), "rows", file=log, flush=True)
print("DONE", file=log, flush=True)
