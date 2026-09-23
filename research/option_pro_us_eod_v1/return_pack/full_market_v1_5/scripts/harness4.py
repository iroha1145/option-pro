"""Offline comparison of strength-ranking variants and gates on a liquid US stock universe.

Inputs: raw/batch_*.parquet from download.py (yfinance, unadjusted OHLC + adj_close).
Outputs: out/*.csv and out/summary.md. Survivorship caveat: current listings only.
"""
import sys, json, math, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
root = Path(sys.argv[1]); out = root / "out4"; out.mkdir(exist_ok=True)
START_EVAL = pd.Timestamp("2022-01-03"); STEP = 5           # rebalance every 5 sessions
TOPN = (20,)
ADV_MIN = 20e6; PRICE_MIN = 5.0; HIST_MIN = 252

# ---------------------------------------------------------------- load
raw = pd.concat([pd.read_parquet(p) for p in sorted((root / "raw").glob("batch_*.parquet"))])
raw["date"] = pd.to_datetime(raw["date"]).dt.tz_localize(None)
raw = raw.drop_duplicates(["date", "ticker"]).sort_values(["ticker", "date"])
def wide(col):
    return raw.pivot(index="date", columns="ticker", values=col).sort_index()
adj = wide("adj_close"); close = wide("close"); high = wide("high"); low = wide("low"); openp = wide("open"); vol = wide("volume")
bench = ["SPY", "QQQ", "IWM", "RSP"]
spy = adj["SPY"].copy()
stocks = [c for c in adj.columns if c not in bench]
adj, close, high, low, openp, vol = (x[stocks] for x in (adj, close, high, low, openp, vol))
factor = adj / close                      # adjustment factor
ahigh, alow, aopen = high * factor, low * factor, openp * factor
print("panel:", adj.shape, "dates", adj.index.min().date(), "→", adj.index.max().date(), flush=True)

# ---------------------------------------------------------------- signals
def ret(n, skip=0):
    return adj.shift(skip) / adj.shift(n) - 1.0
lr = np.log(adj).diff()
r_spy = spy.pct_change()
def spy_ret(n, skip=0): return spy.shift(skip) / spy.shift(n) - 1.0
R = {n: ret(n) for n in (5, 20, 63, 126, 252)}
R["126_21"] = ret(126, 21); R["252_21"] = ret(252, 21)
S = {n: spy_ret(n) for n in (5, 20, 63, 126, 252)}; S["126_21"] = spy_ret(126, 21); S["252_21"] = spy_ret(252, 21)
REL = {k: R[k].sub(S[k], axis=0) for k in R}
vol20 = lr.rolling(20).std() * math.sqrt(252); vol63 = lr.rolling(63).std() * math.sqrt(252); vol126 = lr.rolling(126).std() * math.sqrt(252)
prev = adj.shift(1)
tr = pd.concat([ahigh - alow, (ahigh - prev).abs(), (alow - prev).abs()]).groupby(level=0).max()
atr14 = tr.rolling(14).mean(); atr_pct = atr14 / adj * 100.0
sma20 = adj.rolling(20).mean(); sma50 = adj.rolling(50).mean(); sma100 = adj.rolling(100).mean(); sma200 = adj.rolling(200).mean()
ext_atr = (adj - sma20) / atr14.shift(1)
slope50 = np.log(sma50 / sma50.shift(20)) / (lr.rolling(60).std() * math.sqrt(20))
er63 = (adj - adj.shift(63)).abs() / adj.diff().abs().rolling(63).sum()
t_ma = ((adj > sma50).astype(float) + (sma50 > sma200).astype(float) + (sma50 > sma50.shift(20)).astype(float)) / 3.0 * 100.0
dist52 = adj / adj.rolling(252).max() - 1.0
dd = adj / adj.rolling(63).max() - 1.0; mdd63 = dd.rolling(63).min()
gap = (aopen / prev - 1.0).abs().where(lambda g: g < 0.5)
gap95 = gap.rolling(252, min_periods=200).quantile(0.95)
adv20 = (close * vol).rolling(20).mean()
hist = adj.notna().cumsum()
# residual momentum (t-stat over sessions T-67..T-5 with 252-day rolling beta at T)
rd = adj.pct_change()
cov = rd.rolling(252).cov(r_spy); var = r_spy.rolling(252).var()
beta = cov.div(var, axis=0); alpha = rd.rolling(252).mean() - beta.mul(r_spy.rolling(252).mean(), axis=0)
resid = rd - alpha - beta.mul(r_spy, axis=0)
rs_sum = resid.rolling(63).sum().shift(5); rs_std = resid.rolling(63).std().shift(5)
resid_t = rs_sum / (rs_std * math.sqrt(63))
# Clenow: annualized exp-regression slope × R² over 90 days
def clenow(window=90):
    y = np.log(adj); x = np.arange(window); xm = x.mean(); xv = ((x - xm) ** 2).sum()
    def f(col):
        v = col.values; outv = np.full(len(v), np.nan)
        for i in range(window - 1, len(v)):
            seg = v[i - window + 1:i + 1]
            if np.isnan(seg).any(): continue
            ym = seg.mean(); b = ((x - xm) * (seg - ym)).sum() / xv; a = ym - b * xm
            res = seg - (a + b * x); ss_res = (res ** 2).sum(); ss_tot = ((seg - ym) ** 2).sum()
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            outv[i] = ((math.exp(b)) ** 252 - 1) * r2
        return pd.Series(outv, index=col.index)
    return y.apply(f)
print("computing Clenow slope…", flush=True)
clen = clenow(90)
maxmove90 = rd.abs().rolling(90).max()
# eligibility
elig = (close >= PRICE_MIN) & (adv20 >= ADV_MIN) & (hist >= HIST_MIN) & adj.notna()
def pct(df):  # cross-sectional percentile among eligible, 0-100
    return df.where(elig).rank(axis=1, pct=True) * 100.0
print("ranking…", flush=True)
P = {}
P["r63"], P["r126_21"], P["r252_21"] = pct(REL[63]), pct(REL["126_21"]), pct(REL["252_21"])
P["rel20"] = pct(REL[20])
P["slope"] = pct(slope50); P["negvol"] = pct(-vol20); P["neggap"] = pct(-gap95); P["negmdd"] = pct(-mdd63)
P["resid"] = pct(resid_t); P["dist52"] = pct(dist52); P["volscaled"] = pct(R["126_21"] / vol126)
P["accel"] = pct(REL[20] - REL[63] * (20.0 / 63.0)); P["clen"] = pct(clen)
T_prod = 0.40 * P["slope"] + 0.35 * (50 + 50 * er63).clip(0, 100) + 0.25 * t_ma
R_stab = 0.45 * P["negvol"] + 0.35 * P["neggap"] + 0.20 * P["negmdd"]
M_prod = 0.25 * P["r63"] + 0.40 * P["r126_21"] + 0.35 * P["r252_21"]
V = {}
vs126 = pct(R["126_21"] / vol126); vs252 = pct(R["252_21"] / (lr.rolling(252).std() * math.sqrt(252))); vs63 = pct(R[63] / vol63)
M_vs_mid = 0.6 * vs126 + 0.4 * vs252
M_vs_short = 0.5 * vs63 + 0.5 * vs126
V["V0_current_like"] = 0.50 * P["resid"] + 0.30 * T_prod + 0.20 * R_stab
V["A_prod_like_rawM_T_R"] = 0.42 * M_prod + 0.41 * T_prod + 0.17 * R_stab
V["A_like_rawM_T_noR"] = 0.50 * M_prod + 0.50 * T_prod
V["V16a_Mvs_T30_resid15"] = 0.55 * M_vs_mid + 0.30 * T_prod + 0.15 * P["resid"]
V["V16b_Mvs70_T30"] = 0.70 * M_vs_mid + 0.30 * T_prod
V["V16c_Mvs60_T20_resid10_52w10"] = 0.60 * M_vs_mid + 0.20 * T_prod + 0.10 * P["resid"] + 0.10 * P["dist52"]
V["V16d_Mvs50_T25_R25(half)"] = 0.50 * M_vs_mid + 0.25 * T_prod + 0.25 * R_stab
V["V16e_alpha06_on_prodA"] = 0.42 * (0.4 * M_prod + 0.6 * M_vs_mid) + 0.41 * T_prod + 0.17 * 50.0
V["V16f_alpha085_on_prodA_noR"] = 0.42 * (0.15 * M_prod + 0.85 * M_vs_mid) + 0.41 * T_prod + 0.17 * 50.0
V["V16g_D_like_alpha085"] = 0.50 * (0.15 * P["resid"] + 0.85 * M_vs_mid) + 0.30 * T_prod + 0.20 * 50.0
V["V2_volscaled_mid"] = P["volscaled"]
V["M_vs_mid_only"] = M_vs_mid
V["M_vs_short_only"] = M_vs_short
# gates
med_atr = atr_pct.where(elig).median(axis=1)
G = {
    "none": pd.DataFrame(True, index=adj.index, columns=adj.columns),
    "atr<=1.75xMed": atr_pct.le(1.75 * med_atr, axis=0),
    "atr<=2.5xMed": atr_pct.le(2.5 * med_atr, axis=0),
}
# forward returns
F = {h: adj.shift(-h) / adj - 1.0 for h in (5, 20, 63)}
FS = {h: spy.shift(-h) / spy - 1.0 for h in (5, 20, 63)}
dates = [d for d in adj.index[::STEP] if d >= START_EVAL and d <= adj.index.max() - pd.Timedelta(days=95)]
print("eval dates:", len(dates), dates[0].date(), "→", dates[-1].date(), flush=True)

# ---------------------------------------------------------------- evaluation
rows = []
ic_rows = []
for vname, score in V.items():
    for gname, gate in G.items():
        for n in TOPN:
            recs = []
            prev_set = None
            for d in dates:
                s = score.loc[d].where(elig.loc[d] & gate.loc[d]).dropna()
                if len(s) < n * 3: continue
                top = s.nlargest(n).index
                univ = elig.loc[d]
                rec = {"date": d}
                for h in (5, 20, 63):
                    fr = F[h].loc[d]
                    rec[f"top_{h}"] = fr[top].mean(); rec[f"univ_{h}"] = fr[univ].mean(); rec[f"spy_{h}"] = FS[h].loc[d]
                    rec[f"hit_{h}"] = (fr[top] > FS[h].loc[d]).mean()
                rec["turnover"] = np.nan if prev_set is None else 1 - len(set(top) & prev_set) / n
                rec["pick_vol"] = vol63.loc[d, top].mean(); rec["pick_ext"] = ext_atr.loc[d, top].mean(); rec["pick_atr"] = atr_pct.loc[d, top].mean()
                prev_set = set(top); recs.append(rec)
            df = pd.DataFrame(recs).set_index("date")
            if df.empty: continue
            ex20 = df["top_20"] - df["spy_20"]; ex63 = df["top_63"] - df["spy_63"]
            # non-overlapping 20d chain for drawdown
            chain = (1 + df["top_20"].iloc[::4]).cumprod(); mdd = (chain / chain.cummax() - 1).min()
            row = {"variant": vname, "gate": gname, "topN": n, "n_dates": len(df),
                   "ex20_mean_%": 100 * ex20.mean(), "ex20_t": ex20.mean() / ex20.std() * math.sqrt(len(ex20) / 4.0),
                   "ex63_mean_%": 100 * ex63.mean(), "ex63_t": ex63.mean() / ex63.std() * math.sqrt(len(ex63) / 12.6),
                   "top20_minus_univ20_%": 100 * (df["top_20"] - df["univ_20"]).mean(),
                   "hit20": df["hit_20"].mean(), "hit63": df["hit_63"].mean(),
                   "ann_ret_20d_chain_%": 100 * (chain.iloc[-1] ** (12.6 / len(chain)) - 1), "mdd_20d_chain_%": 100 * mdd,
                   "turnover": df["turnover"].mean(), "pick_vol63_%": 100 * df["pick_vol"].mean(), "pick_ext_atr": df["pick_ext"].mean(), "pick_atr_%": df["pick_atr"].mean()}
            for yr in (2022, 2023, 2024, 2025, 2026):
                sub = df[df.index.year == yr]
                row[f"ex20_{yr}_%"] = 100 * (sub["top_20"] - sub["spy_20"]).mean() if len(sub) else np.nan
            rows.append(row)
    # rank IC (Spearman) of score vs fwd20 / fwd63 among eligible, no gate
    ics = []
    for d in dates:
        s = score.loc[d].where(elig.loc[d]).dropna()
        if len(s) < 200: continue
        q = s.quantile([0.1, 0.9]); hi = s[s >= q[0.9]].index; lo = s[s <= q[0.1]].index
        ics.append({"date": d, "ic20": s.rank().corr(F[20].loc[d].reindex(s.index).rank()), "ic63": s.rank().corr(F[63].loc[d].reindex(s.index).rank()),
                    "ls20": F[20].loc[d, hi].mean() - F[20].loc[d, lo].mean(), "ls63": F[63].loc[d, hi].mean() - F[63].loc[d, lo].mean(),
                    "top10pct_ex20": F[20].loc[d, hi].mean() - FS[20].loc[d], "top10pct_ex63": F[63].loc[d, hi].mean() - FS[63].loc[d]})
    icd = pd.DataFrame(ics).set_index("date")
    ic_rows.append({"variant": vname, "ic20_mean": icd["ic20"].mean(), "ic20_t": icd["ic20"].mean() / icd["ic20"].std() * math.sqrt(len(icd) / 4.0),
                    "ic63_mean": icd["ic63"].mean(), "ic63_t": icd["ic63"].mean() / icd["ic63"].std() * math.sqrt(len(icd) / 12.6),
                    "ls20_%": 100 * icd["ls20"].mean(), "ls20_t": icd["ls20"].mean() / icd["ls20"].std() * math.sqrt(len(icd) / 4.0),
                    "ls63_%": 100 * icd["ls63"].mean(), "ls63_t": icd["ls63"].mean() / icd["ls63"].std() * math.sqrt(len(icd) / 12.6),
                    "top10_ex20_%": 100 * icd["top10pct_ex20"].mean(), "top10_ex20_t": icd["top10pct_ex20"].mean() / icd["top10pct_ex20"].std() * math.sqrt(len(icd) / 4.0),
                    "top10_ex63_%": 100 * icd["top10pct_ex63"].mean(), "top10_ex63_t": icd["top10pct_ex63"].mean() / icd["top10pct_ex63"].std() * math.sqrt(len(icd) / 12.6),
                    **{f"ic20_{yr}": icd[icd.index.year == yr]["ic20"].mean() for yr in (2022, 2023, 2024, 2025, 2026)}})
    print("done", vname, flush=True)
res = pd.DataFrame(rows); res.to_csv(out / "variants.csv", index=False)
icres = pd.DataFrame(ic_rows); icres.to_csv(out / "ic.csv", index=False)

# ---------------------------------------------------------------- conditional: extension & ATR buckets within top-50 raw RS
cond = []
for d in dates:
    s = V["V1_raw_RS"].loc[d].where(elig.loc[d]).dropna()
    if len(s) < 200: continue
    top = s.nlargest(100).index
    e = ext_atr.loc[d, top]; a = atr_pct.loc[d, top]; medA = med_atr.loc[d]
    for h in (5, 20, 63):
        fr = F[h].loc[d, top] - FS[h].loc[d]
        for lo, hi, lab in ((-9, 1, "ext<1"), (1, 2, "ext1-2"), (2, 3, "ext2-3"), (3, 99, "ext>3")):
            m = (e > lo) & (e <= hi)
            if m.sum(): cond.append({"date": d, "dim": "extension", "bucket": lab, "h": h, "ex": fr[m].mean(), "n": int(m.sum()), "hit": (fr[m] > 0).mean()})
        for lab, m in (("atr<=1.75xMed", a <= 1.75 * medA), ("atr1.75-2.5xMed", (a > 1.75 * medA) & (a <= 2.5 * medA)), ("atr>2.5xMed", a > 2.5 * medA)):
            if m.sum(): cond.append({"date": d, "dim": "atr", "bucket": lab, "h": h, "ex": fr[m].mean(), "n": int(m.sum()), "hit": (fr[m] > 0).mean()})
cd = pd.DataFrame(cond)
agg = cd.groupby(["dim", "bucket", "h"]).apply(lambda g: pd.Series({"ex_mean_%": 100 * np.average(g["ex"], weights=g["n"]), "hit": np.average(g["hit"], weights=g["n"]), "avg_n_per_date": g["n"].mean(), "dates": len(g)})).reset_index()
agg.to_csv(out / "conditional.csv", index=False)
print("written", out, flush=True)
