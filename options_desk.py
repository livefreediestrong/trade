"""Listed US equity-option research and isolated paper combos. No broker orders."""
import copy
from datetime import datetime, timedelta, timezone
import os
import re
import threading
import uuid

from flask import Blueprint, jsonify, request

from desk_workbench import finite, fingerprint, timestamp
import paper_loop
from trade_planner import number

STRATEGIES = {
    "long_call": ("C", "long"), "long_put": ("P", "long"),
    "call_debit": ("C", "debit"), "put_debit": ("P", "debit"),
    "call_credit": ("C", "credit"), "put_credit": ("P", "credit"),
}
FIELDS = {"symbol", "expiry", "strategy", "long_strike", "short_strike", "contracts", "budget", "fee_per_contract"}


def now_utc():
    return datetime.now(timezone.utc)


def symbol(value):
    value = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", value):
        raise ValueError("Use a US stock or ETF symbol")
    return value


def plan(body):
    if not isinstance(body, dict) or set(body)-FIELDS:
        raise ValueError("Use only the options ticket fields")
    strategy = body.get("strategy")
    if not isinstance(strategy, str) or strategy not in STRATEGIES:
        raise ValueError("Choose a long call/put or a vertical debit/credit spread")
    expiry = str(body.get("expiry") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry):
        raise ValueError("Choose an expiration date")
    day = datetime.fromisoformat(expiry).date()
    if paper_loop.session_close_time(day) is None:
        raise ValueError("This version requires an exchange-session expiration date")
    count = number(body.get("contracts", 1), "Contracts", 1, 10)
    if count != int(count):
        raise ValueError("Options use whole contracts")
    long = float(number(body.get("long_strike"), "Buy-leg strike", .01, 1_000_000))
    right, kind = STRATEGIES[strategy]
    short = None
    if kind != "long":
        short = float(number(body.get("short_strike"), "Sell-leg strike", .01, 1_000_000))
        lower_long = (right == "C" and kind == "debit") or (right == "P" and kind == "credit")
        if long == short or ((long < short) != lower_long):
            raise ValueError("The buy/sell strikes do not match this spread strategy")
    elif body.get("short_strike") not in (None, ""):
        raise ValueError("A long option has no sell leg")
    return {"symbol": symbol(body.get("symbol")), "expiry": expiry, "strategy": strategy,
            "right": right, "kind": kind, "long_strike": long, "short_strike": short,
            "contracts": int(count), "multiplier": 100,
            "budget": float(number(body.get("budget"), "Maximum paper loss budget", .01, 250)),
            "fee_per_contract": float(number(body.get("fee_per_contract", .65), "Assumed fee per contract per leg", 0, 100))}


def legs(p):
    rows = [{"symbol": p["symbol"], "expiry": p["expiry"], "right": p["right"],
             "strike": p["long_strike"], "action": "BUY"}]
    if p["short_strike"] is not None:
        rows.append(dict(rows[0], strike=p["short_strike"], action="SELL"))
    return rows


def quote_error(p, quote, now, closing=False):
    if not quote.get("ok") or quote.get("source") != "IBKR streaming bid/ask":
        return quote.get("error") or "Verified options data unavailable"
    rows = quote.get("legs") or []
    if len(rows) != len(legs(p)):
        return "Missing option leg"
    for expected, row in zip(legs(p), rows):
        if any(row.get(k) != expected[k] for k in ("symbol", "expiry", "right", "strike", "action")) or row.get("multiplier") != 100 or not row.get("con_id"):
            return "Option contract identity changed or is unsupported"
        bid, ask = finite(row.get("bid")), finite(row.get("ask"))
        if row.get("market_data_type") != 1:
            return "Live options data required; delayed/frozen prices cannot fill"
        if row.get("halted") != 0:
            return "Broker halt status is halted or unavailable; an unhalted status is required to fill"
        if bid is None or ask is None or not 0 < bid <= ask:
            return "Missing, zero or crossed bid/ask"
        if (ask-bid)/((bid+ask)/2) > .25:
            return "Option bid/ask spread exceeds the 25% paper liquidity limit"
        for key in ("bid_observed_at", "ask_observed_at"):
            observed = timestamp(row.get(key))
            if observed is None or not 0 <= now.timestamp()-observed <= 10:
                return "Option quote receipt is stale or in the future"
        side = expected["action"]
        if closing:
            side = "SELL" if side == "BUY" else "BUY"
        size = finite(row.get("ask_size" if side == "BUY" else "bid_size"))
        if size is None or size < p["contracts"]:
            return "Displayed quote size cannot cover all paper contracts"
    return None


def session_error(p, now, closing=False):
    local = now.astimezone(paper_loop.NY_TZ)
    expiry = datetime.fromisoformat(p["expiry"]).date()
    if local.date() > expiry:
        return "Expired contract: settlement is unresolved; this simulator never invents exercise or assignment"
    if not paper_loop.is_rth(now):
        return "Options paper fills wait for regular US stock market hours"
    close = datetime.combine(local.date(), paper_loop.session_close_time(local.date()), paper_loop.NY_TZ)
    if not closing and local.date() == expiry and now >= close-timedelta(minutes=30):
        return "New expiry-day positions stop 30 minutes before the regular close"
    return None


def economics(p, quote, closing=False):
    rows = quote["legs"]
    debit = rows[0]["ask"]-(rows[1]["bid"] if len(rows)==2 else 0)
    closing_value = rows[0]["bid"]-(rows[1]["ask"] if len(rows)==2 else 0)
    width = abs(p["long_strike"]-p["short_strike"]) if len(rows)==2 else None
    if not closing and (p["kind"] in ("long", "debit") and debit <= 0 or p["kind"] == "credit" and debit >= 0):
        raise ValueError("Quoted net premium does not match the chosen strategy")
    if not closing and width is not None and abs(debit) >= width:
        raise ValueError("Quoted premium exceeds spread width; refresh the contracts")
    units = p["contracts"]*100
    fees = p["fee_per_contract"]*p["contracts"]*len(rows)
    max_loss = (debit if p["kind"] in ("long", "debit") else width+debit)*units
    max_gain = None if p["strategy"] == "long_call" else (p["long_strike"]-debit)*units if p["strategy"] == "long_put" else (width-debit)*units if p["kind"] == "debit" else -debit*units
    base = p["long_strike"] if p["kind"] in ("long", "debit") else p["short_strike"]
    break_even = base+(debit+2*fees/units) if p["right"] == "C" else base-(debit+2*fees/units)
    # Credit call/put break-even is measured from the short strike.
    if p["kind"] == "credit":
        break_even = base+(-debit-2*fees/units)*(1 if p["right"] == "C" else -1)
    break_even_possible = break_even >= 0 and (max_gain is None or max_gain-2*fees >= 0)
    return {"entry_debit_per_share": round(debit, 6), "entry_net_usd": round(debit*units, 4),
            "fee_per_side_usd": round(fees, 4), "maximum_loss_usd": round(max_loss+2*fees, 4),
            "maximum_gain_usd": round(max_gain-2*fees, 4) if max_gain is not None else None,
            "break_even_at_expiry": round(break_even, 4) if break_even_possible else None,
            "break_even_possible": break_even_possible,
            "break_even_note": None if break_even_possible else "No attainable break-even after the assumed round-trip fees.",
            "close_value_usd": round(closing_value*units, 4),
            "basis": "Executable-side paper assumptions: buy at ask, sell at bid; simultaneous spread fill is hypothetical. Payoff limits assume both legs are retained through expiry; assignment/stock delivery are not simulated."}


class OptionsDesk:
    def __init__(self, desk):
        self.desk = desk
        self.lock = threading.RLock()
        self.reviews = {}

    @property
    def path(self):
        return self.desk.DATA_DIR/"options_paper.json"

    def load(self):
        raw = self.desk._load_json(self.path, {"starting_cash":1000., "free_cash":1000., "positions":[], "fills":[], "realized_pnl":0.})
        if str(self.path.resolve()) in self.desk._CORRUPT_PATHS or not isinstance(raw, dict) or not isinstance(raw.get("positions"), list):
            raise ValueError("Options paper book is unreadable; restore it before continuing")
        return raw

    def quote(self, p):
        if os.environ.get("BROKER_PROVIDER", "").lower() != "ibkr":
            return {"ok":False,"error":"Connect IBKR for option contract and bid/ask data"}
        import broker_ibkr
        return broker_ibkr.option_quotes(legs(p))

    def status(self):
        with self.lock:
            raw = copy.deepcopy(self.load())
        for pos in raw["positions"]:
            pos["state"] = "expired_unresolved" if now_utc().astimezone(paper_loop.NY_TZ).date().isoformat() > pos["plan"]["expiry"] else "open"
        return {"ok":True,"book":raw,"paper_enabled":bool(self.desk.load_config().get("paper_research_enabled")),
                "live_execution":False,"automatic_options":False,"strategies":list(STRATEGIES),
                "limits":{"initial_simulated_cash":1000,"maximum_loss_per_trade":250,"maximum_positions":5},
                "note":"Separate options paper book. It does not spend stock-paper cash or real money. No exercise, assignment, stock delivery or automatic options orders."}

    def preview(self, body, position_id=None):
        with self.lock:
            raw = self.load()
            position = next((x for x in raw["positions"] if x["id"] == position_id), None) if position_id else None
            if position_id and not position:
                raise ValueError("Open options position not found")
            p = copy.deepcopy(position["plan"]) if position else plan(body)
        quote = self.quote(p)
        if not quote.get("ok"):
            return {"ok":False, "error":quote.get("error") or "Option prices unavailable", "quote":quote}
        error = quote_error(p, quote, now_utc(), bool(position))
        if position and position["contract_ids"] != [r.get("con_id") for r in quote.get("legs",[])]:
            error = "The quoted contracts do not match the held paper position"
        estimate = economics(p, quote, bool(position)) if not error else None
        blockers = [s for s in (error, session_error(p, now_utc(), bool(position))) if s]
        if not position:
            if not self.desk.load_config().get("paper_research_enabled"):
                blockers.append("Paper research is paused")
            if estimate and estimate["maximum_loss_usd"] > min(p["budget"], raw["free_cash"], 250):
                blockers.append("The modeled maximum loss plus fees exceeds the paper budget or free cash")
            if len(raw["positions"]) >= 5:
                blockers.append("Five options positions are already open")
        token = None
        if not blockers:
            token = uuid.uuid4().hex
            with self.lock:
                self.reviews = {k:v for k,v in self.reviews.items() if v["expires"] > now_utc()}
                if len(self.reviews) >= 100:
                    self.reviews.pop(next(iter(self.reviews)))
                self.reviews[token] = {"plan":p,"quote":quote,"estimate":estimate,"position_id":position_id,
                                       "expires":now_utc()+timedelta(seconds=30)}
        realized = round(estimate["close_value_usd"]-position["entry_net_usd"]-position["entry_fee_usd"]-estimate["fee_per_side_usd"],4) if position and estimate else None
        return {"ok":True,"plan":p,"quote":quote,"estimate":estimate,"blockers":blockers,"review_id":token,
                "expires_in_seconds":30 if token else 0,"action":"close" if position else "open", "close_pnl_estimate":realized,
                "live_execution":False}

    def fill(self, token):
        if not isinstance(token, str):
            raise ValueError("Preview the exact paper options ticket first")
        with self.lock:
            raw = self.load()
            previous = next((f for f in raw["fills"] if f.get("review_id") == token), None)
            if previous:
                return {"ok":True,"fill":previous,"duplicate":True}
            review = self.reviews.pop(token, None)
            if not review or review["expires"] <= now_utc():
                raise ValueError("Paper review expired or was already used; preview again")
        p, closing = review["plan"], bool(review["position_id"])
        quote = self.quote(p)  # Fresh read-only quote; never call a broker order router.
        error = quote_error(p, quote, now_utc(), closing) or session_error(p, now_utc(), closing)
        if error:
            raise ValueError(error)
        if fingerprint([r["con_id"] for r in quote["legs"]]) != fingerprint([r["con_id"] for r in review["quote"]["legs"]]):
            raise ValueError("Contract qualification changed; preview again")
        estimate = economics(p, quote, closing)
        if (not closing and estimate["entry_debit_per_share"] > review["estimate"]["entry_debit_per_share"]+1e-8) or (closing and estimate["close_value_usd"] < review["estimate"]["close_value_usd"]-1e-8):
            raise ValueError("Prices moved beyond the reviewed paper limit; preview again")
        with self.desk._lock, self.lock:
            if review["expires"] <= now_utc():
                raise ValueError("Paper review expired while loading prices")
            error = quote_error(p, quote, now_utc(), closing) or session_error(p, now_utc(), closing)
            if error:
                raise ValueError(error)
            raw = self.load()
            position = next((x for x in raw["positions"] if x["id"] == review["position_id"]), None)
            fill = {"id":uuid.uuid4().hex,"review_id":token,"ts":now_utc().isoformat(),"plan":p,"quote":quote,
                    "action":"close" if closing else "open","estimate":estimate,"simulated":True}
            if closing:
                if not position:
                    raise ValueError("Position was already closed; no duplicate fill")
                pnl = round(estimate["close_value_usd"]-position["entry_net_usd"]-position["entry_fee_usd"]-estimate["fee_per_side_usd"],4)
                raw["free_cash"] = round(raw["free_cash"]+position["reserved_cash"]+pnl,4)
                raw["realized_pnl"] = round(raw["realized_pnl"]+pnl,4)
                raw["positions"].remove(position)
                fill.update(position_id=position["id"],realized_pnl=pnl)
            else:
                if not self.desk.load_config().get("paper_research_enabled"):
                    raise ValueError("Paper research was paused while loading prices")
                if len(raw["positions"]) >= 5 or estimate["maximum_loss_usd"] > min(p["budget"],raw["free_cash"],250):
                    raise ValueError("Options paper position or cash limit reached")
                reserved = estimate["maximum_loss_usd"]
                raw["free_cash"] = round(raw["free_cash"]-reserved,4)
                pos = {"id":uuid.uuid4().hex,"opened_at":fill["ts"],"plan":p,"contract_ids":[r["con_id"] for r in quote["legs"]],
                       "reserved_cash":reserved,"entry_net_usd":estimate["entry_net_usd"],"entry_fee_usd":estimate["fee_per_side_usd"]}
                raw["positions"].append(pos)
                fill["position_id"] = pos["id"]
            raw["fills"].append(fill)
            self.desk._save_json(self.path,raw)
        return {"ok":True,"fill":fill,"live_execution":False}


def register(app, desk):
    service = OptionsDesk(desk)
    bp = Blueprint("options_desk",__name__)
    app.config["OPTIONS_AVAILABLE"] = True

    @bp.errorhandler(ValueError)
    def bad_request(exc):
        return jsonify(ok=False,error=str(exc)),400

    @bp.get("/api/options")
    def status():
        return jsonify(service.status())

    @bp.get("/api/options/chain/<underlying>")
    def chain(underlying):
        ticker = symbol(underlying)
        if os.environ.get("BROKER_PROVIDER", "").lower() != "ibkr":
            raise ValueError("Connect IBKR for listed option contracts")
        import broker_ibkr
        result = broker_ibkr.option_chain(ticker)
        return jsonify(result),200 if result.get("ok") else 503

    @bp.post("/api/options/preview")
    def preview():
        result = service.preview(request.get_json(silent=True))
        return jsonify(result),200 if result.get("ok") else 503

    @bp.post("/api/options/close-preview")
    def close_preview():
        body = request.get_json(silent=True)
        if not isinstance(body,dict) or set(body) != {"position_id"} or not isinstance(body["position_id"],str):
            raise ValueError("Select one open options paper position")
        result = service.preview(None,body["position_id"])
        return jsonify(result),200 if result.get("ok") else 503

    @bp.post("/api/options/paper-fill")
    def fill():
        body = request.get_json(silent=True)
        if not isinstance(body,dict) or set(body) != {"review_id"}:
            raise ValueError("Submit only the reviewed paper ticket")
        return jsonify(service.fill(body["review_id"]))

    app.register_blueprint(bp)
    return service
