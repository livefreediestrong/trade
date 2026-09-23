"""Fundamental quality/valuation context from authorized public provider data.

This intentionally is not Morningstar and does not reproduce proprietary ratings.
"""
from __future__ import annotations

import time
from typing import Any


def analyze(symbol: str) -> dict[str, Any]:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return {"ok": False, "error": "ticker_required", "research_only": True}
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        return {"ok": False, "ticker": ticker, "error": str(exc)[:160], "research_only": True}
    def num(name: str) -> float | None:
        value = info.get(name)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    pe = num("trailingPE")
    debt = num("debtToEquity")
    margin = num("profitMargins")
    growth = num("revenueGrowth")
    target = num("targetMeanPrice")
    low = num("targetLowPrice")
    high = num("targetHighPrice")
    uncertainty = "high" if low is not None and high is not None and target and (high - low) / max(abs(target), 1) > 0.5 else "moderate"
    quality = "strong" if (margin is not None and margin > 0.1 and (debt is None or debt < 150)) else "mixed"
    return {
        "ok": True, "ticker": ticker, "research_only": True,
        "label": "fundamental quality/valuation context",
        "source": "yfinance public/provider data", "as_of": time.time(),
        "coverage": {"valuation": pe is not None, "profitability": margin is not None, "leverage": debt is not None, "growth": growth is not None},
        "valuation": {"trailing_pe": pe, "forward_pe": num("forwardPE"), "price_to_sales": num("priceToSalesTrailing12Months"), "price_to_book": num("priceToBook")},
        "profitability": {"profit_margin": margin, "return_on_equity": num("returnOnEquity"), "return_on_assets": num("returnOnAssets")},
        "leverage": {"debt_to_equity": debt, "current_ratio": num("currentRatio")},
        "growth": {"revenue_growth": growth, "earnings_growth": num("earningsGrowth")},
        "dividend": {"yield": num("dividendYield"), "payout_ratio": num("payoutRatio")},
        "analyst_context": {"target_mean": target, "target_low": low, "target_high": high, "dispersion_band": uncertainty},
        "derived": {"quality_band": quality, "uncertainty_band": uncertainty},
    }
