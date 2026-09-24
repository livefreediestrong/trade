# Today at the desk: Fox, Changing Woman, scheduled events and WSB

Implemented September 24, 2026. This ties the broker agent, the event calendar, the WSB threads, the news feeds and the desk's upkeep into one market day. The sidebar companions and the **Today at the desk** panel (Overview and Auto pages) show it.

## Roles

**Fox is the broker agent** (the live agent, `auto_live`; still called Moss in older notes). What he says is what the agent is doing:

- researching a symbol;
- buying, and the stop and target he will protect;
- selling at a stop, target, time limit or before the close;
- holding off during a scheduled event;
- waiting for the open, paused or stuck, with the reason.

Each new trade or exit is announced once, in his speech bubble and in **Fox's recent moves**. Between announcements he gives his current status.

**Changing Woman keeps the chores and reasons with Fox.** Her chores are the desk's own jobs:

- reconciling broker orders;
- reading the Fed, BLS and White House calendars;
- reading the WSB threads and the news feeds;
- syncing the trade journal;
- the nightly upkeep and keeping the ledgers readable.

Each chore shows done, in progress, needs attention or off. Her reasoning notes are what she would tell Fox before he acts:

- a high-impact event is minutes away, or its pause is on now;
- a speech or release later today, and whether it lands after the close;
- WSB is crowding the ticker Fox is looking at, or turning against one he holds;
- whether his latest idea still pays after costs (reward to risk and breakeven win rate), or was held back by a losing record;
- how close today's P&L is to the give-back pause;
- how many minutes remain before the close, and whether he sells first.

A new block or caution note gets her speech bubble at most once every three minutes. She still comments on headlines as before.

The companions only read. Fox trades only through the agent's normal gates, and nothing in this feature can place, size up or approve an order.

## Scheduled speeches and releases (`market_events.py`)

| Source | What | Impact |
| --- | --- | --- |
| [Federal Reserve calendar](https://www.federalreserve.gov/newsevents/calendar.htm) (`/json/calendar.json`) | FOMC statement (2:00 p.m. on the last meeting day), press conference, minutes; speeches and testimony with times; Beige Book | High: FOMC statement and press conference, anything by the Chair. Medium: other governors, minutes, Beige Book. Low: statistical releases |
| [BLS release calendar](https://www.bls.gov/schedule/news_release/) (`bls.ics`, Eastern times) | CPI, Employment Situation, PPI, JOLTS, ECI, productivity, import prices… | High: CPI, jobs report. Medium: PPI, JOLTS, ECI, productivity, import prices |
| President's public schedule (iCalendar; default: Roll Call Factba.se's public White House calendar; `PRESIDENT_SCHEDULE_ICS`) | Addresses, press conferences, remarks, signings | High: address to the nation, State of the Union, joint session, press conference, economic or trade announcements. Medium: remarks, speeches, executive orders. Low: meetings, briefings, travel |
| Your events (Settings → Entry & profit guards) | Anything announced elsewhere, e.g. a scheduled address | Your choice |
| `MARKET_EVENTS_ICS` | Up to six more iCalendar feeds | Medium |

The FOMC decision dates in `macro_calendar.py` are always included, so FOMC days are known even when the feeds are unreachable.

The calendar refreshes every three hours in the background and is cached in `data/market_events.json`. Nothing waits on the network: gates and the panel read the cache. A failing source keeps its last good events and shows as unavailable.

**Event pause.** Around a high-impact event, the broker agent opens nothing new. The pause runs from `event_guard_before_min` before the event (default 15) to `event_guard_after_min` after it (default 15).
- **Pre-market releases:** a release up to two hours before the open (CPI and the jobs report at 8:30) instead pauses the first minutes after the 9:30 open.
- **What keeps running:** research stops during the pause, so it spends no AI budget, but protective exits and reducing orders continue.
- **Manual orders:** your own tickets are not paused; the panel shows the event.
- **Settings:** turn the pause off or change the minutes in Settings.

**Earnings.** With the pause on, Fox also buys nothing in a stock on its earnings day. He also buys nothing when the stock reports before the next session and he is set to hold overnight. Changing Woman notes earnings today or tomorrow for his latest idea.

## WSB threads (`wsb_monitor.py`)

**Which threads.** Every 15 minutes the monitor finds r/wallstreetbets' current Daily Discussion, the nightly "What Are Your Moves Tomorrow", the Weekend Discussion, post-market threads and any other stickied megathread.

**How often.** Every two minutes from 8 a.m. to 5 p.m. Eastern on weekdays (every 10 minutes otherwise, 15 on weekends), it reads each thread's 500 newest comments. That newest-first stream is the live discussion.

**Per ticker it keeps:**
- mentions in the last 15 and 60 minutes, and in the hour before;
- the number of distinct people;
- a rough bullish/bearish lean from plain keywords ("calls", "puts", 🚀, 📉…).

**What counts as a ticker.** A $cashtag, or two to five capitals that are a listed symbol and not WSB slang or an everyday word. One comment counts once per ticker, and bot accounts are ignored. The trend (× the hour before) appears after two hours of continuous reading. A gap resets it.

**Crowded** means at least `wsb_crowd_min_mentions` mentions (default 25) in the last hour from at least a third as many people (at least 8). The ticker must also be spiking to three times the previous hour, or be a top-3 ticker with twice the threshold.

**What happens to a crowded buy** (Settings: `wsb_crowding_action`):
- **half** (default): half the suggested shares. For paper, half the size multiplier. Half of one share means no trade.
- **skip:** not executed.
- **note:** flagged only.
- **off:** WSB is ignored.

It applies to new buy ideas from the scanner, both paper and broker. Sells are never touched. When Reddit is not connected or the data is over 15 minutes old, no caution applies and nothing is blocked. Comment text is never shown to a model; only counts are used.

**Live Chat.** WSB's Live Chat tab runs on Reddit's chat service, which has no public API. The desk does not use a Reddit session to reach it. Paste a chat export under **Buzz → Live chat**, and its tickers count for 30 minutes (shown separately as "pasted chat").

The live WSB read is also a source in the Research page's trend scan, and it has its own table there. General Reddit buzz drops r/wallstreetbets while the live read is fresh, so WSB is not counted twice.

Needs `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` (Reddit OAuth), or `REDDIT_PUBLIC_JSON=1` for the public endpoint, which often refuses automated reads. The rolling mention window is cached in `data/wsb_monitor.json`.

## API

- `GET /api/desk-day`: Fox's state, headline, managed positions and recent moves; Changing Woman's chores and reasoning; upcoming events with pause windows; top WSB tickers. Cached for five seconds.
- `GET /api/market-events`: the next two weeks of events, source status and any active pause.
- `GET /api/wsb`: threads read, top 40 tickers and status.

## Settings (`POST /api/config`)

| Key | Default | Range |
| --- | --- | --- |
| `event_guard_enabled` | true | true/false |
| `event_guard_before_min`, `event_guard_after_min` | 15 | 0–240 |
| `custom_market_events` | [] | up to 50 `{title, start: "YYYY-MM-DD HH:MM" Eastern or ISO with offset, impact}` |
| `wsb_crowding_action` | half | off, note, half, skip |
| `wsb_crowd_min_mentions` | 25 | 5–10000 |

## Limits

- **Calendars can be wrong or late.**
  - The White House schedule is published the morning of and changes often.
  - Unscheduled announcements are not in any calendar. Add them yourself when they are announced.
  - The Fed and BLS feeds were checked against their published formats; this build environment cannot reach them, so on-desk reads still need confirming.
- **WSB is noisy.**
  - Keyword sentiment is rough, and a crowd can be coordinated.
  - Crowding here only makes the agent more careful. It is not a signal to trade.
- **Fox's exits and the event pause only act while the desk and Gateway run.**
