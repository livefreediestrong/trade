"""Claude brain request shape per model, and token costing."""
from types import SimpleNamespace

import pytest

import claude_brain


def test_opus_5_uses_adaptive_thinking_effort_and_refusal_fallbacks(monkeypatch):
    monkeypatch.delenv("CLAUDE_FALLBACKS", raising=False)
    params = claude_brain.request_params("claude-opus-5", "low", "q")
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"]["effort"] == "low"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]


def test_fallbacks_can_be_turned_off_and_are_not_sent_to_other_models(monkeypatch):
    monkeypatch.setenv("CLAUDE_FALLBACKS", "0")
    assert "fallbacks" not in claude_brain.request_params("claude-opus-5", "low", "q")
    monkeypatch.delenv("CLAUDE_FALLBACKS")
    params = claude_brain.request_params("claude-sonnet-5", "low", "q")
    assert "fallbacks" not in params and "betas" not in params


def test_haiku_gets_no_thinking_or_effort():
    params = claude_brain.request_params("claude-haiku-4-5", "low", "q")
    assert "thinking" not in params and "effort" not in params["output_config"]
    assert params["output_config"]["format"]["type"] == "json_schema"


def test_cost_uses_model_price_and_cache_multipliers():
    usage = SimpleNamespace(input_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
                            cache_read_input_tokens=1_000_000, output_tokens=1_000_000)
    assert claude_brain._cost("claude-opus-5-5", usage) == pytest.approx(4 * (1 + 1.25 + 0.1) + 20)
    assert claude_brain._cost("claude-unknown", usage) == pytest.approx(5 * (1 + 1.25 + 0.1) + 25)
