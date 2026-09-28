import importlib.util, json, math, glob, os
from statistics import NormalDist
import numpy as np, pandas as pd
import harness as H
data = H.load()
stocks = [s for s in data if s not in ("SPY", "QQQ")]
O = pd.DataFrame({s: data[s]["Open"] for s in stocks})
z = NormalDist().inv_cdf(1 - 0.05 / 10)
print(f"{'strategy':42s} {'n':>6s} {'fid bps':>8s} {'LB':>7s} {'hold':>7s} {'yrs':>6s} | {'Sharpe':>6s} {'SPY':>5s} {'EW':>5s} {'CAGR':>6s} {'maxDD':>6s} | {'excess vs list':>14s} {'exc LB':>7s} | pass")
for f in sorted(glob.glob("strategies/*.py")):
    n = os.path.basename(f)[:-3]
    if n == "__init__": continue
    rep = json.load(open(f"reports/v2_{n}.json"))
    spec = importlib.util.spec_from_file_location("c", f); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    trades = m.strategy(data)
    exc, keys = [], []
    for t in trades:
        if t["ticker"] in ("SPY", "QQQ"): continue
        if t["entry_date"] not in O.index or t["exit_date"] not in O.index: continue
        a, b = O.loc[t["entry_date"]], O.loc[t["exit_date"]]
        u = (b / a - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if not len(u): continue
        exc.append((t["exit_px"] / t["entry_px"] - 1) - float(u.mean())); keys.append(t["entry_date"].strftime("%Y-%m"))
    if exc:
        g = pd.Series(exc).groupby(keys).mean()
        e_mean = np.mean(exc) * 1e4; e_lb = (g.mean() - z * g.std(ddof=1) / math.sqrt(len(g))) * 1e4
        e_txt, lb_txt = f"{e_mean:+14.1f}", f"{e_lb:+7.1f}"
    else:
        e_txt, lb_txt = f"{'(SPY only)':>14s}", f"{'':>7s}"
    fz, p, b = rep["fidelity_0_commission"], rep["fidelity_0_commission"]["portfolio_5_slots"], rep["benchmarks"]
    print(f"{n:42s} {rep['trades']:6d} {fz['mean_bps']:8.1f} {fz['lower_bound_bps'] if fz['lower_bound_bps'] is not None else float('nan'):7.1f} {fz['holdout_mean_bps']:7.1f} {fz['years_positive']:>6s} | {p['sharpe']:6.2f} {b['spy_buy_hold']['sharpe']:5.2f} {b['equal_weight_buy_hold']['sharpe']:5.2f} {p['cagr']:6.1%} {p['max_drawdown']:6.0%} | {e_txt} {lb_txt} | {rep['passed']}  taken {p['trades_taken']}/{rep['trades']}")
