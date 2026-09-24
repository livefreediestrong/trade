# Wolfram mathematics and strategy-evidence review

September 24, 2026. The user explicitly requested the Wolfram plugin. The semantic context service returned an internal error; the Wolfram Language evaluator then completed the numerical reference calculation successfully. Only synthetic example values were sent. No account records, credentials, personal portfolio, quotes or retained trade journal were uploaded.

## Results

The local functions were compared against the returned Wolfram values using isolated tests. Numerical agreement validates these examples and the stated assumptions, not all inputs, market-data quality or profitability.

| Calculation | Wolfram reference | Local comparison |
| --- | --- | --- |
| Wilson interval, 14 wins / 20, z=1.96 | 0.48102322377102 to 0.85452472603101 | `moss_policy.fit`, absolute tolerance 1e-12 |
| Beta(5,5) posterior mean after 14/20 | 0.63333333333333 | Ordinary posterior arithmetic; the desk's policy score is separately 0.7 at n=20 |
| Policy sample weights at n=5,9,10,15,19,20; prior strength=10 | 0.33333333333333; 0.47368421052632; 0.5; 0.8; 0.96551724137931; 1 | Actual `moss_policy.fit` with qualified synthetic test records, tolerance 1e-12 |
| Long break-even, 9 shares at 100, total fees 2, zero slippage | 100.22222222222 | `trade_planner.estimate`, six-decimal output |
| Short break-even, 10 shares at 100, total fees/borrow 5, zero slippage | 99.5 | `trade_planner.estimate` |
| Peak 100,000, equity 97,000, no cash flows | 0.03 drawdown | Actual isolated `RiskManager.update_portfolio` |
| Max drawdown .05, min .01, target volatility .02, observed .04 | 0.025 threshold | Actual paper kernel latches its drawdown halt at 3%; no broker attached |
| European call, S=K=100, T=1 year, r=.05, sigma=.2, dividend=0 | 10.450583572186 per share | `research_metrics.option_value`, tolerance 1e-10 |
| Corresponding European put | 5.5735260222570 per share | Same function and tolerance |

The fee examples assume known fixed fees and zero slippage. The production estimator also has slippage and unknown-fee paths; unknown net totals remain unknown. Model option values do not certify broker prices, American exercise, assignment or execution costs. The isolated kernel is a separate paper/replay component, not a newly enabled live risk gateway.

## Findings and presentation fixes

1. **Independence was overstated in a UI label.** Removed “independent” from the qualified-outcome count. Rejecting overlapping ticker horizons does not remove common market shocks or all serial dependence. The existing session-day bootstrap remains explicitly exploratory.
2. **Sample weighting was easy to mistake for capital allocation.** Renamed the table's “Alpha allocation” to “Sample weight.” Added observed win rates and their existing Wilson bounds beside cohort results.
3. **The policy-adjusted score differs from a pure posterior.** With a neutral Beta(5,5) prior, 14/20 produces a posterior mean of 63.33%; the user-selected full-weight-at-20 policy uses 70%. The backend legacy key `posterior_win_rate` is retained for compatibility, but the new explanation labels this distinction. No ranking or execution-policy change was made.
4. **The paper-scaling explanation was imprecise.** It said “positive lower confidence bound”; the code requires a lower win-rate bound greater than 50%, plus positive net expectancy, sample threshold and half-sample stability. The text now says what the code enforces.

The app includes a collapsed “Math & evidence checks · Wolfram reference” section in Research studio, with links from Moss's evidence rules. Hover/focus help carries the short explanation of sample weighting. These are saved reference checks, not a permanent runtime connection to Wolfram. There is no new paid-model or data request in the trading path.

## Data and strategy boundary

The existing evidence suite exercises rejection of mock data, unknown provenance, future timestamps, out-of-session observations, stale fills, unfilled attempts and overlapping ticker horizons. It tests as-of outcome visibility. Chronological evaluation purges outcomes unavailable at the test boundary; its record retains the tested thresholds. Passing these cases does not prove the external source itself is correct, nor establish a strategy edge from an unavailable or insufficient live sample.

Wolfram is useful for independent formula checks, parameter sensitivity and distribution calculations. Broker/source timestamps, entitlements, corporate actions, actual fills and commissions still require their original evidence. Strategy evaluation needs after-cost outcomes, preserved train/test separation, multiple session days, relevant benchmarks and out-of-sample testing. A formula or a 20-outcome count cannot certify future profit.

Sources: [NIST's Wilson interval formula](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm), [Wolfram BetaDistribution documentation](https://reference.wolfram.com/language/ref/BetaDistribution.html), and the actual Wolfram Language evaluation below. No source claims to verify this app's market feed.

## Reproducible Wolfram Language query

```wolfram
z=196/100; n=20; w=14; p=w/n;
center=(p+z^2/(2n))/(1+z^2/n);
radius=z Sqrt[p(1-p)/n+z^2/(4n^2)]/(1+z^2/n);
alpha[n_]:=Piecewise[{{1,n>=20},{n/(n+10),n<10}},
  n/(n+10)+(n-10)/10*(1-n/(n+10))];
cdf[x_]:=(1+Erf[x/Sqrt[2]])/2;
d1=(Log[100/100]+(5/100+(2/10)^2/2))/(2/10); d2=d1-2/10;
ExportString[N[<|
 "wilson_14_of_20"->{center-radius,center+radius},
 "beta_posterior_14_of_20_prior_5_5"->19/30,
 "policy_score_14_of_20"->7/10,
 "alpha_by_count"->Table[{k,alpha[k]},{k,{5,9,10,15,19,20}}],
 "long_break_even_9_shares_100_entry_2_total_fees"->100+2/9,
 "short_break_even_10_shares_100_entry_5_total_fees"->100-5/10,
 "drawdown"->(100000-97000)/100000,
 "dynamic_drawdown_limit"->Max[1/100,Min[5/100,(5/100)*(2/100)/(4/100)]],
 "european_call_100_100_1y_r05_iv20"->100 cdf[d1]-100 Exp[-5/100] cdf[d2],
 "european_put_100_100_1y_r05_iv20"->100 Exp[-5/100] cdf[-d2]-100 cdf[-d1]
|>,14],"RawJSON"]
```

## Verification

`python -m pytest tests/test_math_reference.py tests/test_moss_workday.py -q`: 40 passed, one pre-existing eventkit event-loop deprecation warning. Eleven added reference cases cover the computed constants and the actual local functions. Tests use isolated data; no live execution commands were run.
