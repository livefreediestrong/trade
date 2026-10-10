"""python run_one.py <path/to/module.py> <report_name> <M>  -> writes reports/<report_name>.json, prints summary."""
import importlib.util, json, sys, time
import harness as H
path, name, m = sys.argv[1], sys.argv[2], int(sys.argv[3])
spec = importlib.util.spec_from_file_location("cand", path); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
data = H.load()
t0 = time.time(); trades = mod.strategy(data); t1 = time.time()
rep = H.evaluate(name, trades, data, m_tests=m)
rep["runtime_sec"] = round(t1 - t0, 1)
rep["first_trades"] = [{k: (str(v.date()) if hasattr(v, "date") else v) for k, v in t.items()} for t in sorted(trades, key=lambda t: t["entry_date"])[:5]]
json.dump(rep, open(f"reports/{name}.json", "w"), indent=1, default=str)
f = rep.get("fidelity_0_commission", {})
print(json.dumps({"trades": rep["trades"], "gross_mean_bps": rep.get("gross_mean_bps"), "fidelity": {k: f.get(k) for k in ("mean_bps", "lower_bound_bps", "years_positive", "holdout_trades", "holdout_mean_bps", "portfolio_5_slots")},
                  "ibkr_mean_bps": rep.get("ibkr_110_account", {}).get("mean_bps"), "benchmarks": rep.get("benchmarks"), "passed": rep["passed"], "reasons": rep["reasons"], "runtime_sec": rep["runtime_sec"]}, default=str))
