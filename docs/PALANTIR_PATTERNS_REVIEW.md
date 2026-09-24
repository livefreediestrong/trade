# Palantir patterns worth adapting to Tomahawk / Moss

Reviewed 2026-09-23 against public primary documentation and current local source. This is a product and architecture review, not a hands-on benchmark of a Palantir deployment. Suggestions below are our adaptations, not claims that Palantir supplies profitable trading strategies. No runtime code, trading settings or broker actions changed.

## Recommendation

Prioritize a linked decision dossier, an attention inbox, and a reproducible evaluation/promotion workflow. Retain the small Flask/Jinja/vanilla-JavaScript UI. Use stable IDs and a modest SQLite evidence index to connect the data already retained. Avoid an enterprise-platform rewrite.

## Products reviewed

| Product/application | Documented pattern | Adaptation for this app |
| --- | --- | --- |
| Foundry Ontology | Objects, properties, links, functions and governed actions represent the operational domain. | Link instruments, market observations, research runs, signals, risk decisions, orders, fills, outcomes and strategy versions. |
| Workshop | Object-oriented operational apps, consistent widgets, task inboxes and shared operational views. | One selected instrument or decision opens a compact inspector; an inbox shows only items needing attention. |
| AIP Chatbot Studio | Assistants use application state, retrieved objects/documents and tools under access controls. | Moss receives the currently selected instrument, account workspace, time range and permitted evidence, and cites the exact records used. |
| AIP Evals | Explicit test cases, evaluators, result inspection and parameter experiments. | Fixed evaluation datasets, walk-forward comparisons, source-fidelity checks and a model/prompt/policy scorecard. |
| AIP Evolve | A target, goal, validation strategy and bounded agent search produce a reviewed change proposal. | Moss proposes research-policy improvements within a budget; compare candidate and baseline before any promotion. |
| Automate | Conditions checked continuously or on schedules trigger effects, with execution history. | Plain-language rules with next run, last run, reason for skipping, retry state and exact permitted effect. |
| Workflow/Data Lineage and Health Checks | Dependency inspection, source-to-output tracing and resource-health checks. | Show which provider/record/rule caused a block, and all dependent research affected by a stale feed. |
| Gotham/Foundry interactivity | Paired applications share selected objects/context; docs name Gaia and Graph integration. | Carry one selection through chart, news, notebook, relationship view and execution journal. Graph-specific investigation is a secondary view. |
| Apollo | Managed deployments and release channels, including stable/canary channels. | Version manifest, startup health checks and recovery to a known-good application release. Protect durable broker/order state during rollback. |
| Blueprint | Apache-2.0 React toolkit for data-dense desktop interfaces. | Reference tables, filters, drawers and keyboard interactions; defer a React dependency until the eventual UI redesign. |

## Current-source inventory

- `desk_workbench.py:122` already groups descriptive evaluation by model, prompt version, workspace and horizon, with coverage, abstention and Brier scoring. Its own output explicitly distinguishes descriptive evidence from held-out validation.
- `desk_workbench.py:282` already links an equity symbol to daily bars, news and saved theses. This is not a unified instrument/decision inspector across equities, options and fills.
- `desk_workbench.py:314` already freezes experiment capture, policy and engine version; replay checks a SHA-256 fingerprint. These captures are valuable foundations to preserve.
- `research_companion.py:186` saves daily observations, account context, model review and training summaries. It retains 90 notebook entries. This is not a permanent per-operation execution trace or a full searchable evidence index.
- `real_trade_journal.py:99` retains execution records and correction relationships, and enriches eligible equities with a quote reference. Existing identities should be reused rather than creating another fill ledger.
- `quant_models.py`, `quant_risk.py`, `quant_engine.py` have typed contracts, durable SQLite reservations and an independent watchdog in a separate paper-only module. They are not connected to the running workday/IBKR path.
- `Start-Tomahawk.ps1` already launches/checks the application and coordinates Gateway startup. An actual tested release rollback and data-migration policy would be additional work.

## Ranked backlog and acceptance checks

### 1. Decision dossier and evidence links — highest foundational value, medium scope

One action opens the source observations, market/receipt timestamps, model and prompt version, research conclusion, risk result, order identity, fills, costs and measured outcome. Show missing links explicitly. Distinguish market time, observed/ingested time and decision time to protect against lookahead.

Start with a read-only index over current retained records. Stable IDs must include workspace/account scope. Options require contract identity (underlying, expiry, strike, put/call, multiplier and broker contract ID where verified), not ticker-only joins. Retain broker corrections/supersession; do not silently rewrite the original decision evidence.

Acceptance: a retained fill can be traced to its actual review and available research inputs; unrelated accounts/workspaces/contracts cannot join; revised data never replaces what was originally observed. Unknown links stay unknown.

### 2. Attention inbox and contextual inspector — high visible value, small-to-medium scope

Aggregate repeated statuses into one incident with first/last occurrence, affected workflows and next useful action. Examples: missing daily P&L; a stalled quote feed; paper rule skipped because spread is too wide; an outcome ready for review. Distinguish research opportunities from operational failures. Silence/reviewing an alert must not dismiss the underlying risk gate.

Keep one page, live/paper identities always visible and critical blocking text outside hover-only helpers. On selection, load a side inspector on desktop and an inline section on narrow screens. Preserve an open order review's bound terms even when research selection changes. Keep Moss's fox in spare space.

Acceptance: one underlying outage produces one updated item; no duplicate toast storm; status is understandable without color; selecting a research row never modifies an order or its approval.

### 3. Evaluation suites and candidate promotion — highest learning value, medium scope

Build on current evaluation/captures: versioned datasets, chronological train/validation/test splits, strict point-in-time inputs, source checks, leakage checks, separate real/paper cohorts, comparison against simple baselines, friction sensitivity, latency and model cost. Use deterministic metrics for realized performance; a language-model judge can review explanation quality but must not certify P&L.

Record every attempted candidate to avoid reporting only the winner of a parameter search. Consider dependence between outcomes, market regimes and repeated use of the same test set. Twenty outcomes are a configuration milestone, not proof of a durable trading edge.

Candidate stages: proposed -> offline evaluated -> paper observation -> reviewed. A candidate can fail or remain inconclusive. Promotion changes should be versioned and reversible; no automatic live enablement.

Acceptance: compare baseline and candidate on the same untouched evidence, identify training versus evaluation records, reproduce deterministic metrics, and retain failed experiments. A profitable paper sample alone cannot promote a strategy to real-money execution.

### 4. Observable Moss runs — high value, medium scope

For each research run, save input record IDs, permitted tool operations, results/errors, model/prompt version, wall-clock duration, cost, output and concise decision rationale. This is an observable execution trace, not private model chain-of-thought. Link the trace from Moss's notebook.

A local model can summarize or search the same evidence. Memory is indexed observations plus separately fitted ranking parameters; neither a bigger prompt nor stored notes updates foundation-model weights automatically.

Acceptance: a user can see what data supported a sentence and whether the data was current at the time; unsupported citations are rejected; failed runs do not appear successful.

### 5. Rule-based automation view — medium-to-high value, medium scope

Make existing schedules legible as trigger -> conditions -> effect -> result. Example: regular session open AND fresh quote AND sufficient liquidity AND paper risk capacity -> evaluate one eligible paper candidate. Show last run, next run, skipped reason, retry limit and cost budget. Conditions/effects are typed and server validated.

Build adapters around existing Moss routines; do not add another scheduler that competes for the same paper book. Rules and the UI must explicitly distinguish research, local paper and real broker actions.

Acceptance: replay a rule against fixed inputs, explain each failed condition, deduplicate retries, and prove a paper action cannot invoke a broker write.

### 6. Data-health dependency view — high troubleshooting value, medium scope

Show provider -> observations -> affected strategies -> blocked action, including stale versus missing versus contradictory values. Add reason codes and freshness thresholds alongside plain-language descriptions. A system can have a connected broker and still lack P&L or an eligible quote.

Acceptance: distinguish socket/connection health from data validity; no fallback from missing values to zero; a root feed failure explains dependent research failures.

### 7. Scenario comparison — medium value, medium scope

Extend the frozen replay workbench with side-by-side capital, fee, spread, delay and policy assumptions; display differences from baseline. Keep immutable historical evidence separate from interactive what-if edits.

Current Palantir Ontology scenarios are beta and are explicitly not historical snapshots; they automatically rebase on changing base data. Workshop's older scenario system is marked legacy. Borrow isolated experimentation, but not auto-rebasing semantics for trading evaluation.

Acceptance: changing assumptions cannot change the original dataset or live configuration; simulated output remains labeled; repeated deterministic replay has the same result.

### 8. Bounded improvement proposals — medium value after evaluation is reliable

Adapt AIP Evolve: Moss can propose a hypothesis, eligible records, one versioned parameter change, test plan, result, uncertainty and suggested next experiment. Limit iterations, model cost and parameter ranges. It should not choose its own metric after seeing results or quietly change the risk policy.

Acceptance: proposal contains baseline/candidate comparison, all attempted variants and evidence IDs; acceptance cannot arm live trading.

### 9. Relationship investigation — useful later, data-dependent

Explore verified links among instrument, issuer, sector, ETF membership, earnings event, option contract and positions. Keep factual relationships separate from estimated correlations. Each link needs source, effective date and confidence/uncertainty appropriate to its type.

A selective relationship panel is more useful initially than a full-screen network graph. News or language-model suggestions should not become verified supplier/customer links without supporting data. Acquisition/licensing and historical coverage of relationship data are real dependencies.

### 10. Release and recovery controls — valuable reliability work, medium-to-large scope

Add a version/config/schema manifest, tested application health checks and reversible release installation around the existing launcher. Keep broker connectivity stable during UI edits. If process separation is later justified, broker connection, research worker and web UI should expose their own health; this is a scoped reliability project, not a prerequisite for the UI improvements above.

Rolling back code does not roll back executed trades. Recover software while preserving and reconciling the durable broker/order journal. Never replay an order submission merely because a process restarted.

## UI direction

One page: persistent account/session/data strip, attention list, selected-instrument workspace, and contextual inspector. Detailed evidence, scenario comparison and historical graphs open only when requested. Keep the subdued green palette and fox; use consistent typography, aligned numeric columns, clear labels, visible focus and few primary actions. Configuration belongs in collapsed sections. Secondary definitions remain hover/focus/tap helpers; critical trading conditions remain visible.

Palantir's own Workshop design guidance recommends limiting choices and overcrowding. Dense does not need to mean constantly showing every capability. Blueprint can inform interaction design, but adding React now would conflict with the current lightweight, easily replaceable frontend.

## Sources

1. [Ontology concepts](https://www.palantir.com/docs/foundry/ontology/overview/)
2. [Workshop overview and inbox patterns](https://www.palantir.com/docs/foundry/workshop/overview)
3. [Workshop design guidance](https://www.palantir.com/docs/foundry/workshop/application-design-best-practices)
4. [AIP Chatbot Studio, formerly Agent Studio](https://www.palantir.com/docs/foundry/chatbot-studio/overview)
5. [AIP Evals suites](https://www.palantir.com/docs/foundry/aip-evals/create-suite)
6. [AIP Evals experiments](https://www.palantir.com/docs/foundry/aip-evals/experiments)
7. [AIP Evolve](https://www.palantir.com/docs/foundry/aip-evolve/overview)
8. [Automate](https://www.palantir.com/docs/foundry/automate/overview)
9. [Workflow Lineage](https://www.palantir.com/docs/foundry/workflow-lineage/overview)
10. [Data Lineage](https://www.palantir.com/docs/foundry/data-lineage/overview)
11. [Health checks](https://www.palantir.com/docs/foundry/health-checks/overview)
12. [Gotham/Foundry shared application context](https://www.palantir.com/docs/foundry/cross-app-interactivity/overview)
13. [Apollo release orchestration](https://www.palantir.com/docs/apollo/core/introduction)
14. [Ontology scenarios beta and historical-snapshot limitation](https://www.palantir.com/docs/foundry/ontology/overview-ontology-scenario)
15. [Legacy Workshop scenarios](https://www.palantir.com/docs/foundry/workshop/scenarios-overview)
16. [Blueprint source, scope and license](https://github.com/palantir/blueprint)

Only this review document was added. No runtime tests were run because application code and configuration were not changed.
