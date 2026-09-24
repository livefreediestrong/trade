"""Persistent, read-only broker execution journal; separate from paper learning."""
import copy
from collections import defaultdict
from datetime import datetime, timezone
import os
import re
import threading
import time

from desk_workbench import finite, timestamp
import paper_loop


def review(rows):
    active = [r for r in rows if not r.get("superseded_by") and not r.get("voided") and r.get("verified")]
    currencies = defaultdict(lambda: {"executions":0,"fees_reported":0.,"fees_missing":0,
                                     "broker_realized_pnl":0.,"pnl_missing":0,"positive_pnl_executions":0,"negative_pnl_executions":0})
    orders = defaultdict(list)
    for row in active:
        unit = row.get("commission_currency") or row.get("currency") or "Unknown"
        group = currencies[unit];group["executions"] += 1
        fee, pnl = finite(row.get("commission")), finite(row.get("broker_realized_pnl"))
        if fee is None: group["fees_missing"] += 1
        else: group["fees_reported"] += fee
        if pnl is None: group["pnl_missing"] += 1
        else:
            group["broker_realized_pnl"] += pnl
            group["positive_pnl_executions"] += int(pnl > 0)
            group["negative_pnl_executions"] += int(pnl < 0)
        orders[(row["account_id"],row["paper_mode"],row.get("permanent_order_id") or (row.get("client_id"),row.get("order_id")))].append(row)
    for values in currencies.values():
        values["fees_reported"] = round(values["fees_reported"],6)
        values["broker_realized_pnl"] = round(values["broker_realized_pnl"],6)
    outside = sum(not paper_loop.is_rth(datetime.fromtimestamp(timestamp(r["ts"]),timezone.utc)) for r in active)
    slippage = [r["reference_slippage_bps"] for r in active if finite(r.get("reference_slippage_bps")) is not None]
    missing_fees = sum(v["fees_missing"] for v in currencies.values())
    missing_pnl = sum(v["pnl_missing"] for v in currencies.values())
    multiple = sum(len(v)>1 for v in orders.values())
    insights = [f"{len(active)} executions across {len(orders)} recorded orders; {multiple} orders have multiple fills.",
                f"{missing_fees} executions lack reported fees and {missing_pnl} lack reported realized P&L. Review totals as incomplete." if missing_fees or missing_pnl else "Fees and reported realized P&L are present for retained executions; account-history coverage is still limited.",
                f"{outside} executions occurred outside regular US stock hours; retained for review, excluded from paper personality training."] if active else ["No actual executions are available to analyze yet."]
    if slippage:
        insights.append(f"Mean price difference versus a recorded pre-trade quote: {sum(slippage)/len(slippage):.2f} bps across {len(slippage)} executions; positive means adverse. This is not a broker routing benchmark.")
    return {"executions":len(active),"orders":len(orders),"orders_with_multiple_executions":sum(len(v)>1 for v in orders.values()),
            "by_currency":dict(currencies), "insights":insights,
            "outside_regular_session":outside,
            "slippage_observations":sum(r.get("reference_slippage_bps") is not None for r in active),
            "note":"Execution counts include partial fills. Broker-reported realized P&L and fees are shown separately; no second fee subtraction or invented cost basis. Missing fees/P&L are excluded from sums, not treated as zero. No full round-trip win rate is inferred."}


class TradeJournal:
    def __init__(self, desk):
        self.desk=desk
        self.lock=threading.RLock()
        self.sync_lock=threading.Lock()
        self.next_sync=0.
        self.last_error=None

    @property
    def path(self):
        return self.desk.DATA_DIR/"broker_execution_journal.json"

    def load(self):
        raw=self.desk._load_json(self.path,{"executions":[],"last_sync":None,"error":None})
        if not isinstance(raw,dict) or not isinstance(raw.get("executions"),list):
            raise ValueError("Broker trade journal is unreadable")
        return raw

    def status(self):
        with self.lock:
            raw=self.load()
        # The displayed account label and its totals must describe the same book.
        identity=raw.get("identity") or {}
        selected=[r for r in raw["executions"] if r.get("account_id") == identity.get("account_id")
                  and r.get("paper_mode") is identity.get("paper_mode")]
        real=[r for r in selected if r.get("paper_mode") is False]
        paper=[r for r in selected if r.get("paper_mode") is True]
        accounts=defaultdict(list)
        for row in raw["executions"]:
            accounts[(row.get("account_id"),row.get("paper_mode"))].append(row)
        return {"last_sync":raw.get("last_sync"),"error":self.last_error or raw.get("error"),"coverage":raw.get("coverage"),
                "account_suffix":str(identity.get("account_id") or "")[-4:],
                "scope":"Most recently verified synchronized account; historical accounts remain separate",
                "accounts":[{"account_suffix":str(account or "")[-4:],"paper_mode":mode,"review":review(rows)}
                            for (account,mode),rows in accounts.items()],
                "real":review(real),"broker_paper":review(paper),"executions":sorted(real,key=lambda r:r["ts"],reverse=True)[:100],
                "excluded":raw.get("excluded",0),"read_only":True}

    def tick(self):
        if time.monotonic() < self.next_sync:
            return
        self.next_sync=time.monotonic()+60
        self.sync()

    def sync(self):
        if not self.sync_lock.acquire(blocking=False):
            return
        try:
            if os.environ.get("BROKER_PROVIDER","").lower() != "ibkr":
                return
            import broker_ibkr
            response=broker_ibkr.execution_history()
            self.merge(response)
            self.last_error=None
        finally:
            self.sync_lock.release()

    def merge(self, response):
        now=datetime.now(timezone.utc)
        with self.lock:
            raw=self.load()
            if str(self.path.resolve()) in self.desk._CORRUPT_PATHS:
                raise ValueError("Restore broker trade journal before syncing")
            if not response.get("ok"):
                raw["error"]=response.get("error") or "Broker execution feed unavailable"
                self.desk._save_json(self.path,raw)
                return
            identity=response.get("identity") or {}
            rows={(r["account_id"],r["paper_mode"],r["execution_id"]):r for r in raw["executions"]}
            signals={s.get("id"):s for s in self.desk.load_signals()}
            rejected=0
            for item in response.get("executions",[]):
                revision=re.fullmatch(r"(.+)\.(\d+)",str(item.get("execution_id") or ""))
                voided=(item.get("voided") is True and finite(item.get("shares")) == 0
                        and revision is not None and int(revision[2]) > 1)
                if (item.get("account_id") != identity.get("account_id") or item.get("paper_mode") is not identity.get("paper_mode") or
                    not item.get("verified") or not item.get("execution_id") or timestamp(item.get("ts")) is None or
                    timestamp(item["ts"]) > now.timestamp() or (not voided and ((finite(item.get("shares")) or 0) <= 0 or (finite(item.get("price")) or 0) <= 0))):
                    rejected+=1;continue
                key=(item["account_id"],item["paper_mode"],item["execution_id"])
                prior=rows.get(key,{})
                merged=dict(prior,**copy.deepcopy(item),first_seen=prior.get("first_seen",now.isoformat()),last_seen=now.isoformat())
                # A reconnect may temporarily lack a commission previously observed.
                for field in ("commission","commission_currency","broker_realized_pnl"):
                    if (merged.get(field) is None and prior.get(field) is not None
                            and not (field == "broker_realized_pnl" and item.get("broker_realized_pnl_reported") is False)):
                        merged[field]=prior[field]
                signal=signals.get(item.get("order_ref"))
                if (signal and item.get("asset_type")=="STK" and (signal.get("review_identity") or {}).get("account_id")==item["account_id"]
                    and (signal.get("review_identity") or {}).get("paper_mode") is item["paper_mode"] and signal.get("ticker")==item.get("ticker")):
                    if signal.get("execution_benchmarks"):
                        merged["execution_benchmarks"] = copy.deepcopy(signal["execution_benchmarks"])
                        merged["strategy"] = signal.get("llm_model")
                        merged["order_type"] = (signal.get("review_order") or {}).get("type")
                        merged["intended_shares"] = (signal.get("review_order") or {}).get("shares")
                    quote=signal.get("quote") or {}
                    reference=finite(quote.get("price"));stamp=timestamp(quote.get("market_time"))
                    age=timestamp(item["ts"])-stamp if stamp is not None else None
                    if reference and age is not None and 0 <= age <= 30:
                        merged.update(reference_price=reference,reference_time=quote["market_time"],reference_age_sec=age,
                                      reference_slippage_bps=round((item["price"]-reference)/reference*10000*(1 if item["side"]=="buy" else -1),4),
                                      strategy=signal.get("llm_model"))
                rows[key]=merged
            # IBKR correction reports differ only in the final execution-id segment.
            families=defaultdict(list)
            for key,row in rows.items():
                match=re.fullmatch(r"(.+)\.(\d+)",row["execution_id"])
                if match:
                    families[(key[0],key[1],match[1])].append((int(match[2]),row))
            for family in families.values():
                if len(family)<2: continue
                newest=max(family,key=lambda pair:pair[0])[1]
                for _,row in family:
                    if row is not newest: row["superseded_by"]=newest["execution_id"]
            raw.update(executions=list(rows.values()),last_sync=now.isoformat(),error=None,identity=identity,
                       coverage=response.get("coverage"),excluded=rejected+response.get("excluded",0))
            self.desk._save_json(self.path,raw)
