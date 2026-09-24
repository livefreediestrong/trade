# Local intelligence for Moss

Hardware checked 2026-09-23: i7-13620H, approximately 32 GB RAM, NVIDIA RTX 4060 Laptop GPU with 8 GB VRAM. Ollama is installed; its default local API was not responding during the check. No model was installed, started, benchmarked or connected by this change.

## Recommended first use

Add a local, read-only notebook reviewer. Retrieve a few relevant saved research/actual-fill records, show their timestamps and IDs, and ask for a brief structured explanation: observations, uncertainties, sources, next research question. Good tasks include explaining terms, summarizing a session, finding analogous historical setups and checking whether a claim is supported by recorded evidence. Feed it actual records and computed metrics; do not ask the language model to manufacture prices, compute portfolio risk, infer missing fees or override execution policy.

I would start with `qwen3.5:4b`: the [official Ollama catalog](https://ollama.com/library/qwen3.5) lists a 3.4 GB download, compared with 6.6 GB for 9B. A short 4K-8K context should leave substantially more GPU headroom on this laptop than the 9B model. This is a sizing recommendation, not measured latency or guaranteed fit: runtime buffers and context cache also consume memory. The advertised 256K context is not a practical target for this machine.

Use SQLite full-text search before adding an embedding service or Redis. The current notebook and actual-execution journal can become an evidence index; retrieval memory does not retrain a language model's weights. The existing fitted research-ranking parameters are separate statistical estimates. Neither memory nor a conversational personality guarantees a profitable strategy.

## Proposed local-only contract

- Fixed loopback endpoint `http://127.0.0.1:11434`, explicitly selected installed local model; no cloud fallback or remote model tag.
- Start Ollama with `OLLAMA_NO_CLOUD=1` and `OLLAMA_HOST=127.0.0.1:11434`. Restart it after changing settings. [Official local-only configuration](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features).
- Schema-constrained response validated by Pydantic. Use [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs); validate all cited record IDs against the retrieved set. Schema compliance alone cannot establish truth.
- No trading/configuration tools in the local model context. Bounded inputs/output/timeouts, cache by evidence hash, and visible “local model unavailable” status on failures.
- No automatic downloads, account data upload or changes to the current configured research model.

Before enabling daily reviews: evaluate a fixed set of stored sessions, missing-data cases and misleading news snippets; measure source fidelity, false claims, useful abstentions, latency and memory. Keep out-of-sample sessions separate from any prompt tuning. This document is the recommendation and integration contract; a local reviewer is not yet running in the app.
