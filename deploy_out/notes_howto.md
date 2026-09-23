# How to use Tomahawk — in-app tutorial

## Goal
Clear **How to** tutorial users can open anytime, plus optional first-visit show once. Does not re-enable `#desk-guide` (left forced hidden).

## Files changed
- `templates/index.html` — `#btn-howto` in topbar chrome; `#howto-modal` dialog; Advanced intro one-liner points to How to
- `static/app.js` — `openHowto()` / `closeHowto()`; wire button / Esc / backdrop; first-visit ~400ms; light focus trap
- `static/app.css` — chill slate howto modal + pill How to button (no wool/Pendleton)
- `deploy_out/notes_howto.md` — this note

## First-run behavior
1. On DOM ready, if `localStorage.tomahawk_howto_seen` is missing → open modal after ~400ms.
2. On any close (Got it / Close / Esc / backdrop): if “Show this once…” is checked (default), set `tomahawk_howto_seen=1`.
3. Uncheck that box before close → do **not** set seen (will show again next visit). Prefer default path so it is not naggy.
4. `#btn-howto` always reopens the tutorial in Simple and Advanced.
5. Does not block Start, Auto paper, or trading.

## Copy outline
- **Title:** How to use Tomahawk
- **Intro:** Paper research desk at this computer. Fake money by default. Not live brokerage advice.
- **Steps (8):** open URL → Simple/Advanced → Goal+Cash+Start → call tile research-only → Ask me first / Waiting → Auto fill → Stop / Force flatten → optional alerts, friction, Find, radar
- **Close:** Got it + Close; Esc; backdrop click
- **Checkbox:** Show this once when I open the desk (default on)
- **Adv intro add-on:** “Open **How to** anytime for a short walkthrough.”

## Verify (mental)
- How to opens from button; Esc / backdrop / Got it / Close dismiss
- First visit once via `tomahawk_howto_seen=1`
- Button always works after seen
- `#desk-guide` stays hidden (`updateDeskGuide` unchanged)
- `node --check static/app.js` OK
- No loop/trading logic changes; no git commit

## Deploy
Parent deploys to PC (restart signal desk / hard-refresh static). App URL: http://127.0.0.1:5056
