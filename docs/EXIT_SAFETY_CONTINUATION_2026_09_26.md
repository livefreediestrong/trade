# Exit-safety continuation — September 26, 2026

This repair batch strengthens existing exits and broker protection evidence. It does not implement automatic broker-held stop creation or the complete options close/assignment lifecycle; G02 and G03 remain open. Existing account/risk settings and live activation are preserved.

## Confirmed defects and repairs

- **Lost tracked positions on unreadable holdings:** missing/non-list positions, invalid or nonfinite quantities and duplicate symbols could be treated as an empty or smaller holding. Fox now keeps the record, reports holdings as unverified and waits. Account/activation changes during reads discard the result. The native stock and option readers reject account/connection changes before returning a successful snapshot.
- **Older observations overwriting new fills:** a holdings read crossing a later fill could delete or shrink the newer tracking record. Updates now compare against the record they inspected. Protective exit signals bind the exact tracked position; changes/corrections before the final gate require a fresh exit decision.
- **Stock sales removing short-call cover:** all IBKR stock sales now check held short calls, pending call sells and existing stock sells on the same underlying. Only surplus shares can be sold. A pending buyback has not released shares. The reverse path also rejects a new covered call when a working stock sell/stop already claims the needed shares.
- **Independent option closes removing a possible hedge:** a single-leg STC is held for review when another short option or working sell on the same underlying/right may depend on it. The desk cannot prove the intended hedge from a list of contracts and does not invent a safe pairing. The existing complete-spread close path still verifies both legs and quantities.
- **Overstated broker-stop evidence:** the read-only monitor now includes order conditions, activation times and algorithm fields. Missing details, conditional/future stops, competing stock sells and short/working calls prevent the simple full-coverage message. They produce combined-order/position review notes instead.

The share/hedge checks are conservative: they do not net long option hedges, assume portfolio-margin permission, cancel existing broker orders or automatically close short calls. Adjusted or unknown call sizes and working same-underlying spreads require broker review. Existing external orders can still change/fill after a check; only an accepted broker order is broker-held protection, and neither its existence nor these software tests guarantee execution.

## Validation

- Interim isolated run after the holdings repair: 17 passed and 10 failed. The remaining failures reproduced missing call-cover interlocks and overstated stop evidence; the actual adapter reached intercepted submission when it should have refused.
- After those repairs: 219 focused broker lifecycle, option risk/uncertainty, agent/exit and protection tests passed.
- Final new-fill/correction changes: 139 relevant agent and exit tests passed, including all 42 new continuation cases. Counts overlap and must not be added together.
- Existing broker-protection and live-agent frontend checks passed. They cover stale status, activation boundaries and controller behavior; the new financial checks are exercised in Python with broker transport intercepted.
- No complete Python-suite rerun is claimed for this batch. A Windows test-harness attempt was interrupted because blocking sockets also blocked creation of an asyncio worker's local socketpair. The new tests explicitly run intercepted adapter bodies on the fixture thread; separate existing queue tests own thread behavior. The interrupted run is not counted as evidence of a product failure or a pass.

Artifacts under the task workspace `outputs`: `exit-safety-interim-20260926.txt`, `exit-safety-focused-20260926.txt`, `exit-safety-matrix-20260926.txt`, and `exit-safety-final-targeted-20260926.txt`.

## Broker references

[IBKR's Order reference](https://www.interactivebrokers.com/docs/tws-api/ref/order) defines activation times, order conditions, parent links, OCA behavior and transmission. A Submitted/PreSubmitted label alone does not establish that an ordinary unconditional stop covers the current position. [IBKR's agreement explanation](https://ndcdyn.interactivebrokers.com/Universal/servlet/Registration.formSampleView?file=registration_1%2Fuk_customer_agreement.html) describes the obligation created by a written option and the difference between owning and not owning its underlying; this is used here as a risk concept, not as a statement about this account's jurisdiction or contract terms.

## Open acceptance work

Broker-held protective creation, rejected/partial child orders, replacements, disconnect/restart recovery, assignment reconciliation and market-open endurance still need implementation and broker-paper qualification. The source changes and isolated tests do not activate the agent or establish a profitable strategy.

## Deployment and live read-only checks

- Created and verified `state-20260926T194131-e7f2a787.zip` at 15:41 ET: 29 stores, 34,342,024 bytes, SHA-256 `04983eac508bb4526ca3368c40ae5bf22466bb417a822d4b6460f6d5a97e0d1c`. No production restore occurred.
- Before the desk reload, Gateway was connected with a numeric Daily P&L callback and an empty actual broker snapshot. Mode was manual, Fox disabled, with no decision in flight or unresolved app submission. Used the existing normal shutdown and launcher with `-NoBrowser -NoBroker -NoDialogs`; Gateway was left running.
- At 15:48 ET the new PID 25580 reported healthy build `0aa17fee332bac46`, equal to disk. Manual mode, disabled Fox and saved configuration remained unchanged. Configuration SHA-256: `7053A7E80C9BAAC9EDC122BB1BAFC6B63EDF9077EB93B26378C4ABBBDA0A2B28`.
- Gateway remained connected and a fresh native snapshot reported zero held instruments and zero working orders. Daily P&L nevertheless failed to arrive on the new subscription. One in-socket refresh and one soft API reconnect completed without restoring a callback. Account-download recovery timed out; positions completed. P&L remains unknown and new risk blocked. The earlier numeric callback is not reused as fresh P&L.
- The bounded, account-redacted `data/diagnostics/ibkr-pnl.jsonl` records an initial callback at 15:22:51 ET, desk reconnection at 15:42:58, silent subscription retry, and the 15:47 soft reconnect without a new callback. This reproduces G01 and does not establish whether the cause is Gateway or its upstream service. The earlier unexplained Gateway process exit is still a separate unresolved observation.
- Real browser verification on Overview and Fox showed the new version, live OFF, missing-P&L explanation and the current empty-account protection report. The active browser viewport reported 579 × 956 CSS pixels with no horizontal overflow or captured console errors; no responsive layout changes or full width-matrix rerun are claimed. No order, cancellation, risk-setting change or Gateway restart was performed. An empty production account cannot exercise the conditional-stop/option-obligation scenarios covered by isolated tests.

Deployment receipt: task workspace `outputs/exit-safety-runtime-20260926.json`. Browser screenshot: `outputs/exit-safety-browser-20260926.png`. The primary remaining queue is `TASKS.md`.
