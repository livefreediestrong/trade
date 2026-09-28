# Full workspace, companion conversations and weekly learning

September 26, 2026. Implements the approved design foundation and cumulative weekly review. The unresolved broker/risk qualification work remains in [TASKS.md](../TASKS.md); this release does not activate trading.

## How to use it

The View selector on every page defaults to **Full tools & research**. All existing tools remain available. **Focused essentials** folds secondary sections; it uses the same forms, records and actions. Open a folded section whenever needed. Research links reveal their target section automatically. Practice and Review a trade are the clearer navigation labels for the existing paper and ticket pages.

In the existing **Appearance** disclosure, choose **Desert** or **Plain**. Light/dark is independent, and **Show portraits** hides artwork while retaining companion names, chat and functionality. Existing users keep the Desert default. Unknown skin names or corrupt preferences fall back to Plain. Preferences are saved in this browser; changing them does not affect trading configuration. Additional reviewed theme packs can use the same boundary later. Unrestricted draggable dashboards and installable theme scripts are not included.

**Chat with companions** is available on every page. Choose Changing Woman, Fox or both. They can explain saved information, question a conclusion and propose research. The conversation has no order, cancellation, configuration or memory-writing tools. Fox retains his separate existing automated-decision role within the deterministic risk checks; conversation text cannot override it.

Three reply choices are explicit:

| Choice | What happens |
| --- | --- |
| Gemini + local explanations | The three standard explanation buttons use local text. Open questions use the configured Gemini model and existing budget. |
| Local explanations only | The standard explanations work without a model. Unsupported open questions ask you to choose AI explicitly. |
| Local AI · Ollama | Choose an already installed model. Generation stays on this PC; no model download or paid fallback is performed. |

The selected reply provider and local model persist across pages/reloads; choosing local AI does not silently revert to Gemini. An unsent message draft is retained in this tab’s session storage across closing/reopening and reloading; sending clears it.

Select a company in Research to link its chart, company check and chat stock field. Slow results from an older selection cannot replace the newer company. This does not change the execution ticket. Chat uses dated saved research; it does not independently fetch a current quote.

Overview adds a compact brief: latest recorded Fox activity, an exact-symbol match among fresh watched/held-company headlines, and the next calendar event. An empty match is not proof that no relevant news exists. Fox adds a concentration display using available USD stock marks divided by account value. Missing marks and purchase-cost substitutes are excluded and identified. The highlight threshold is only a display preference; it is not a risk-limit change. Options, sector classification and foreign-currency exposure remain outside that display.

Settings adds one connection/routine summary. It distinguishes missing access, denied access, payment/budget limits, rate limits and partial coverage. Last successful read and next retry are shown only when the source supplies them. An old broker result is labelled old after 45 seconds. This summary consumes the existing event streams.

## At the end of the week

The app schedules a review **90 minutes after the final US stock-market session of the week**: normally Friday at 5:30 p.m. Eastern. Holidays, early closes and daylight saving changes use the existing exchange calendar. The computer must be awake and the app running. On return it catches up on the latest missed week; it does not fabricate every missing week. It waits for the daily review and saves durable attempt receipts before requesting either model interpretation.

Local calculations combine individual records for the whole week, using current corrections and withdrawals ahead of archived versions. This avoids averaging daily averages with unequal sample sizes. The comparison window is the preceding four calendar weeks. Daily coverage, sessions without observations, missing dates/costs, overlapping records and data exclusions remain visible. Valid losses and unchanged results count as evidence too.

The report also uses retained broker execution evidence under the journal's current account identity. It keeps real fills, broker-paper fills and simulated price observations separate. It does not invent an account-wide return, equity curve, deposit-adjusted gain or complete execution history. Qualifying a price observation does not qualify the entire trading policy or broker lifecycle.

Research combines saved, dated daily material with bounded current publisher feeds and official release excerpts. Up to 35 weekly headlines and eight documents are retained; this is not unrestricted internet search, full paid-article access or Reddit ingestion. Later publication cannot establish knowledge at entry. Missing days remain missing.

Changing Woman reviews the evidence; Fox challenges it and suggests the next test. The two roles use the same configured Gemini service and are not independent models validating each other. Each review uses the existing shared daily spending limit and a separate $0.25 estimated weekly-review ceiling on its dispatch day. This is an estimate, not a guaranteed provider invoice cap. Incomplete or invalid replies stay unavailable; completed role attempts are not automatically paid for again after restart.

Open **The week in review** on Fox or Research. Keep up to 52 reports, with dates, sources, the frozen evidence and original AI text available in details. Historical reports remain snapshots. Later corrections affect future calculations instead of silently rewriting what the earlier review knew.

## What memory and learning mean

| Kind | Retention and meaning |
| --- | --- |
| Conversation | Up to 20 conversations, 30 turns each, 30 days; up to 12 recent turns are sent to a model. Older visible turns may be outside its context. Scoped to account and real/practice workspace. |
| Explicit saved note | Up to 100 notes, 700 characters each, 180 days since save/correction. You can inspect, edit, forget or export them. They are user statements, not verified facts or trading rules. |
| Research evidence | Existing dated records and correction/withdrawal controls remain the source of measured outcomes. Missing values are not zero. |
| Daily review | Existing 90-report retention and unproven hypotheses remain intact. |
| Weekly review | Up to 52 frozen reports; four prior weekly hypothesis summaries can inform the next review, clearly labelled unproven. |

Editing or forgetting a saved note removes the old version from future recall. Earlier assistant replies that relied on that version are also excluded from future model context. They can remain in the visible historical conversation. An in-flight reply using a revoked note is withheld. Forgetting a conversation does not automatically forget separately saved notes. Backup copies can retain older text until removed under the backup policy; chat is not a secure password vault.

The existing local research ranking can use measured observations. This release does **not** fine-tune Gemini or a local language model, promote a weekly idea into a live rule, raise risk caps, or treat companion agreement as extra evidence. Test proposed changes on later data, compare after costs, count valid losses, and retain negative results. More observations alone do not prove profitability. Full-policy and broker-execution validation remain separate open work.

## Local AI on this PC

The existing installation has about 32 GB RAM, an RTX 4060 Laptop GPU and installed Gemma/Qwen models. No model was downloaded. Gemma `gemma4:26b` was tested with cloud access disabled. The final three synthetic cases took approximately 26.7–27.7 seconds each. A later real chat through the running app took 42.01 seconds and correctly used the current weekly sample; local replies can be noticeably slower than Gemini. Local AI is therefore an optional private conversation tool, not a fast execution component. Daily/weekly reviews still use Gemini as previously chosen.

The first evaluation exposed a wrong definition: the model associated qualified outcomes with successful trades. The prompt was corrected, a known-error rejection was added, and the three cases were repeated. Final cases covered missing prices, valid losing/flat evidence, and an instruction hidden in supplied source text. These limited checks do not prove general accuracy. All model prose remains interpretation.

The adapter accepts only installed local GGUF models at `127.0.0.1:11434`, refuses cloud-model metadata and redirects, ignores proxy environment variables, limits context and output, runs one local request at a time and unloads the model after each reply. It never downloads a model or switches to cloud after a local failure. A long conversation may require starting a new chat.

Ollama was already installed and was started for this evaluation with `OLLAMA_NO_CLOUD=1`. To start the same local-only service again after closing it, use a PowerShell window with these commands, then leave that window/service running:

```powershell
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
```

If Ollama is already listening, use that instance instead of starting a duplicate. The app independently restricts requests to verified installed local models. See [Ollama's local-only setting](https://docs.ollama.com/faq) and [chat API](https://docs.ollama.com/api/chat).

## Development and verification

The initial separation adds some work, but future development stays in the existing Flask/Jinja and vanilla JavaScript project. There is one set of components and execution logic. Theme tokens, optional scenery and view preferences are presentation. `nadzeel.css` still supplies shared component geometry during incremental migration; removing that file outright would break layout. New themes should supply reviewed tokens/assets rather than scripts or trading behavior.

New modules: `weekly_review.py`, `companion_chat.py`, `local_chat.py`; shared templates/scripts add the new panels. Daily/weekly review reuse durable model-attempt handling. Conversation state and weekly reports are top-level stores covered by the existing verified backup mechanism.

Validation: the full Python suite passed 1,398 tests; the final sentence-level claim-check refinement also passed its focused regression. Four existing datetime deprecation warnings remain. All 29 frontend suites passed. Browser checks covered all six pages in both main themes at requested widths 1341 (browser reports 1342), 1024 and 390, plus light-theme checks, mobile chat/draft retention, note editing, hidden portraits, folded-section links and reduced-motion sprites. No horizontal overflow or duplicate IDs was observed in those matrices. These checks are not a complete WCAG/200% zoom audit.

The isolated browser server denied outbound network before application import and labelled its prepared model replies explicitly. Those replies were not counted as real AI. Real Gemma evaluation was separate. A real Gemini chat attempt failed while the weekly Gemini pair completed; chat now records safe error categories, applies the existing bounded thinking setting to its limited response, and withholds incomplete replies. Final provider retest and deployment receipts are recorded below.

An isolated backup was actually restored into a new folder. Six stores included one conversation and one weekly fixture; real application readers passed with network denied before import, workers disabled and all restored checksums unchanged. Production was not restored. The pre-release production backup contained 27 stores. New report/conversation stores also passed backup inclusion checks.

Broker protection, partial fills/cancel races, options expiry/assignment, prospective full-policy evidence, sustained account recovery and actual risk-policy values remain the D/G work in TASKS. Unit tests, model replies and attractive screens are not broker-paper qualification. Live automation stays OFF.


## Real provider and deployment receipts

The September 21–25 weekly review completed with two real Gemini responses. It retained 35 dated headlines and three official document records (an unavailable document remains marked unavailable). One of five daily reports was present; four sessions had some observations, but only 12 observations over two sessions passed the existing data checks. Their average after recorded costs was -27.38 basis points, or about -0.274%, per simulated price observation. The four-week comparison contained zero usable observations. The conclusion remains **More evidence needed**, not live profitability or an account return. This is distinct from the stricter full-policy qualification still open in G08.

Inspection found two model statements overstating an empty journal as proof of no trading. A sentence-level local check now withholds those statements on read without changing the original stored report. The final display includes the reasons and clickable source receipts. These known-claim checks do not verify every model sentence.

After bounded chat reasoning and clearer errors were added, two real Gemini conversation turns completed in 5.07 and 5.08 seconds, at estimated response costs of $0.003112 and $0.004043. The follow-up recalled the explanation and used the current twelve-observation/two-session sample. The initial generic runtime failure's precise provider reason was not retained, so the response-limit cause remains an inference; future failures retain safe error categories. No automatic retry or higher spending limit was introduced. Mixed known/unknown citation lists are rejected.

The actual browser completed a local Gemma reply using the running app, with zero API model cost and no cloud fallback. This is stronger evidence than the earlier isolated prepared reply, but still a small answer-quality sample.

Verified production backups before release reloads: `state-20260926T041051-3fc0bf71.zip` (27 stores) and `state-20260926T041756-76f68113.zip` (29 stores, including the new weekly and conversation stores). Gateway was already unavailable before the release reloads; port 4001 refused connections. No current holdings/P&L claim is made from that disconnected state. Saved execution mode remained manual and live automation remained OFF.

Task evidence folder: `C:\Users\maher\Documents\Codex\2026-09-24\new-chat\outputs\experience-20260926`. Contains real-provider receipts, browser matrices/screenshots, isolated restore receipt and final pytest output. The restore fixture and prepared browser replies are labelled separately from real-provider evidence.


Final loaded build: `99b4beda3c50b203`, verified equal to the on-disk fingerprint. Final production snapshot `state-20260926T043133-5ffe7abe.zip` was restored into a separate copy and exercised through real application readers with network blocked and workers disabled. All 29 stores were checksum-unchanged, including three conversations and the completed weekly report. No production state was restored or replayed. This copy contains private app state and must stay local.

Total recorded estimated AI cost for September 26 at final verification: $0.062365 (six calls, including the initial failed chat, diagnostic call, two successful conversation turns and two weekly reviews). Vendor invoices remain authoritative. The app continues to display broker unavailable / real trading OFF. The isolated QA server was stopped after verification; the production desk and optional local Ollama service remain available.
