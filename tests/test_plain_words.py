"""Fox's move log is reworded for display only; recorded events keep the screener's text."""
import desk_day


def test_screener_shorthand_becomes_plain_words():
    assert desk_day.plain_words("Market data or setup assessment unavailable for SIXC; retrying after the saved research interval") == \
        "Couldn't get price data for SIXC; he'll try again later"
    text = desk_day.plain_words("No qualifying setup for ULTY (AVOID: Relative volume only 0.60x (need ≥1.5x). No unusual interest yet — skip.)")
    assert text == ("Looked at ULTY, no trade (screener said avoid: trading was only 60% of its usual volume; he needs 150%. "
                    "No unusual interest yet, so he skipped it.)")
    watch = desk_day.plain_words("No qualifying setup for XLP (WATCH: Intraday session not today (latest=2026-09-24). Rel vol 1.20x — WATCH, not AVOID.)")
    assert watch == ("Looked at XLP, no trade (screener said watch only: Today's trading data isn't in yet (last session 2026-09-24); "
                     "trading at 120% of usual volume.)")


def test_other_lines_are_left_alone():
    for line in ("Bought 5 AAPL at $100.00.", "Waiting on the broker for MSFT: pending"):
        assert desk_day.plain_words(line) == line
