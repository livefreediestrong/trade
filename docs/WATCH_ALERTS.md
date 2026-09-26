# Watchlist alerts

Settings page → **Watchlist alerts**. Every 5 minutes the desk checks the stocks Fox holds, then your watchlist (40 stocks at most), and sends a desk alert when:

| Alert | When | Example |
| --- | --- | --- |
| Big move | The price is up or down at least *N*% from yesterday's close (default 3%). It fires again at 2×N and 3×N. | "AAA is up 3.5% today ($100.00 → $103.50). $1,000 of AAA would now be worth about $1,035." |
| Earnings | The stock reports earnings within the next *N* days (default 2; 0 = off). Dates are checked every 6 hours. | "BBB reports earnings tomorrow (2026-09-26). Prices often jump or drop sharply on the report." |
| Headline | A high-impact headline (earnings, guidance, deals, FDA or lawsuits, offerings, bankruptcy) about the stock in the last 2 hours. Only news the desk already fetched counts; no extra requests are made. | "AAA headline: AAA raises guidance (google_news)" |

- Alerts go through `desk_alerts`: they show as a desk alert and as a browser notification if you allowed notifications. They also go to `ALERT_WEBHOOK_URL` (Discord or Slack) when that is set.
- On a volatile day, at most 6 move alerts go out per check, biggest move first; the rest are grouped into one summary alert ("5 more followed stocks moved at least 3% today: …"). Prices under $1 show four decimals.
- Each alert is sent once. Its key (symbol, day, direction and size band, earnings date or headline) is remembered for 7 days in `data/watch_alerts.json`, so a restart does not repeat it.
- **Notification only (owner decision):** alerts never place, size or block a trade. Their settings are stored apart from trading configuration.
- Routes: `GET/POST /api/watch-alerts` (settings: `enabled`, `move_pct` 1–50, `earnings_days` 0–14, `news`), `POST /api/watch-alerts/check` (check now). The background check is skipped when `TOMAHAWK_NO_BG=1`.
