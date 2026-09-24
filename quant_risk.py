"""Durable risk reservation and deterministic sizing. No broker imports or I/O."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
import sqlite3
import time
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

import paper_loop
from quant_models import Fill, Order, Portfolio, RiskConfig, RiskDecision, Tick, Signal

D = Decimal
ACTIVE = ('reserved', 'working', 'partial', 'unknown', 'cancel_pending')


class RiskStore:
    """One owner/event loop, durable SQLite transactions; amounts are decimal strings."""

    def __init__(self, path: Path, account: str, starting_cash: Decimal = D('1000')) -> None:
        if not account or not starting_cash.is_finite() or starting_cash <= 0:
            raise ValueError('A named paper account and positive initial balance are required')
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,signal_id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL,status TEXT NOT NULL,filled TEXT NOT NULL DEFAULT '0');
            CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS positions(symbol TEXT PRIMARY KEY,quantity TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,event TEXT NOT NULL,detail TEXT NOT NULL);
        ''')
        self.account = account
        with self.db:
            if self.get('account') not in (None, account):
                raise ValueError('Database belongs to another paper account')
            if self.get('account') is None:
                self.put('account', account)
                self.put('cash', str(starting_cash))
                self.put('peak', '0')
                self.put('cash_flow', '0')
                self.put('halt', '')
            # Do not reset existing equity/high-water/halt state on restart.
            for key in ('cash', 'peak', 'cash_flow'):
                if self.get(key) is None or not D(self.get(key)).is_finite():
                    raise ValueError('Risk database contains invalid monetary state')

    def get(self, key: str) -> str | None:
        row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        self.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

    def audit(self, event: str, detail: str, now: datetime) -> None:
        self.db.execute('INSERT INTO audit(at,event,detail) VALUES (?,?,?)', (now.isoformat(), event, detail))

    def active(self) -> list[tuple[Order, str, Decimal]]:
        rows = self.db.execute("SELECT * FROM orders WHERE status IN ('reserved','working','partial','unknown','cancel_pending')").fetchall()
        return [(Order.model_validate_json(r['payload']), r['status'], D(r['filled'])) for r in rows]

    def order(self, order_id: str) -> tuple[Order, str, Decimal]:
        row = self.db.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
        if row is None:
            raise ValueError('Untracked order')
        return Order.model_validate_json(row['payload']), row['status'], D(row['filled'])

    def status(self, order_id: str, status: str) -> None:
        if status not in (*ACTIVE, 'filled', 'cancelled', 'rejected'):
            raise ValueError('Unknown order state')
        _, previous, _ = self.order(order_id)
        if previous not in ACTIVE and status != previous:
            raise ValueError('Terminal order cannot be reactivated')
        with self.db:
            self.db.execute('UPDATE orders SET status=? WHERE id=?', (status, order_id))

    def positions(self) -> dict[str, Decimal]:
        return {r['symbol']:D(r['quantity']) for r in self.db.execute('SELECT * FROM positions') if D(r['quantity']) > 0}

    def record_paper_fill(self, fill: Fill) -> bool:
        """Atomically store a simulated execution, residual reservation, cash and holding."""
        with self.db:
            previous = self.db.execute('SELECT payload FROM fills WHERE id=?', (fill.id,)).fetchone()
            if previous:
                if previous[0] != fill.model_dump_json():
                    raise ValueError('Conflicting duplicate execution')
                return False
            order, state, filled = self.order(fill.order_id)
            if state not in ACTIVE or any(getattr(fill,k) != getattr(order,k) for k in ('account','symbol','side','environment')):
                raise ValueError('Fill identity or lifecycle mismatch')
            if not order.quote_at < fill.market_at <= fill.received_at or fill.received_at >= order.expires_at:
                raise ValueError('Execution timestamps are not causal or exceed the order deadline')
            if filled+fill.quantity > order.quantity:
                raise ValueError('Execution exceeds the unfilled quantity')
            if (order.side=='buy' and fill.price > order.limit_price) or (order.side=='sell' and fill.price < order.limit_price):
                raise ValueError('Execution violates the limit price')
            cash=D(self.get('cash'))
            held=self.positions().get(order.symbol,D(0))
            value=fill.quantity*fill.price
            next_cash=cash-value-fill.fee if fill.side=='buy' else cash+value-fill.fee
            next_held=held+fill.quantity if fill.side=='buy' else held-fill.quantity
            if next_cash < 0 or next_held < 0:
                raise ValueError('Simulated execution would overdraw cash or create a short')
            self.db.execute('INSERT INTO fills VALUES (?,?)',(fill.id,fill.model_dump_json()))
            self.db.execute('UPDATE orders SET filled=?,status=? WHERE id=?',
                (str(filled+fill.quantity),'filled' if filled+fill.quantity==order.quantity else 'partial',order.id))
            self.db.execute('INSERT INTO positions VALUES (?,?) ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity',
                (order.symbol,str(next_held)))
            self.put('cash',str(next_cash))
            self.audit('paper_fill',fill.id,fill.received_at)
        return True

    def close(self) -> None:
        self.db.close()


class RiskManager:
    def __init__(self, config: RiskConfig, store: RiskStore,
                 monotonic: Callable[[],float] = time.monotonic) -> None:
        self.config, self.store, self.monotonic = config, store, monotonic
        self.last_valid: dict[str, tuple[datetime,float]] = {}
        self.quotes: dict[str, Tick] = {}
        self.started = monotonic()
        self.drawdown = D(0)
        self.drawdown_limit = config.max_drawdown
        # An interrupted submission may already exist at a service. Retain its reservation.
        if store.active():
            self.trip('restart_requires_reconciliation',datetime.now(timezone.utc))

    @property
    def halted(self) -> str:
        return self.store.get('halt') or ''

    def trip(self, reason: str, now: datetime) -> None:
        with self.store.db:
            if not self.halted:
                self.store.put('halt',reason)
                self.store.audit('risk_halt',reason,now)

    def observe(self, tick: Tick, now: datetime) -> bool:
        age=(now-tick.market_at).total_seconds()
        receipt_age=(now-tick.received_at).total_seconds()
        prior=self.last_valid.get(tick.symbol)
        if (not 0 <= age <= self.config.max_tick_age or not 0 <= receipt_age <= self.config.max_tick_age
                or tick.halted or not paper_loop.is_rth(tick.market_at) or not paper_loop.is_rth(now)
                or any(w in tick.source.lower() for w in ('mock','synthetic','fixture'))
                or (prior and tick.market_at <= prior[0])):
            return False
        self.last_valid[tick.symbol]=(tick.market_at,self.monotonic())
        self.quotes[tick.symbol]=tick
        return True

    def update_portfolio(self, portfolio: Portfolio, now: datetime) -> bool:
        if portfolio.account != self.store.account or not 0 <= (now-portfolio.as_of).total_seconds() <= self.config.max_tick_age:
            self.trip('invalid_or_stale_portfolio',now)
            return False
        old_peak=D(self.store.get('peak'))
        flow_delta=portfolio.net_cash_flow-D(self.store.get('cash_flow'))
        peak=max(portfolio.equity,old_peak+flow_delta)
        self.drawdown=max(D(0),(peak-portfolio.equity)/peak)
        self.drawdown_limit=max(self.config.min_drawdown,min(self.config.max_drawdown,
            self.config.max_drawdown*self.config.target_volatility/portfolio.volatility))
        with self.store.db:
            self.store.put('peak',str(peak))
            self.store.put('cash_flow',str(portfolio.net_cash_flow))
        if self.drawdown >= self.drawdown_limit:
            self.trip('portfolio_drawdown',now)
        return not bool(self.halted)

    def watchdog(self, now: datetime) -> list[str]:
        """Latch on a stalled required feed and identify reservations requiring cancel."""
        active=self.store.active()
        reference=max((v[1] for v in self.last_valid.values()),default=self.started)
        stale=self.monotonic()-reference >= self.config.heartbeat_timeout
        for order,_,_ in active:
            seen=self.last_valid.get(order.symbol)
            if not seen or self.monotonic()-seen[1] >= self.config.heartbeat_timeout:
                stale=True
        if stale:
            self.trip('stale_data_heartbeat',now)
        if self.halted:
            return [o.id for o,_,_ in active]
        return [o.id for o,_,_ in active if now >= o.expires_at or
                (now-o.created_at).total_seconds() >= self.config.max_order_latency]

    def decide(self, signal: Signal, tick: Tick, portfolio: Portfolio, now: datetime) -> RiskDecision:
        def reject(reason: str) -> RiskDecision:
            with self.store.db:self.store.audit('risk_reject',f'{signal.id}: {reason}',now)
            return RiskDecision(signal_id=signal.id,approved=False,reason=reason,
                                drawdown=self.drawdown,drawdown_limit=self.drawdown_limit)
        self.update_portfolio(portfolio,now)
        if self.halted:return reject(self.halted)
        if signal.symbol != tick.symbol or signal.market_at != tick.market_at:return reject('signal_quote_mismatch')
        if not signal.created_at <= now < signal.expires_at:return reject('signal_expired_or_future')
        if not paper_loop.is_rth(now):return reject('outside_regular_session')
        if not 0 <= (now-tick.market_at).total_seconds() <= self.config.max_tick_age:return reject('stale_tick')
        seen=self.last_valid.get(tick.symbol)
        if not seen or self.quotes.get(tick.symbol) != tick or self.monotonic()-seen[1]>=self.config.heartbeat_timeout:
            return reject('unverified_or_stalled_feed')
        if signal.volatility_samples < self.config.min_volatility_samples:return reject('insufficient_volatility_samples')
        if self.store.db.execute('SELECT 1 FROM orders WHERE signal_id=?',(signal.id,)).fetchone():
            return reject('duplicate_signal')
        active=self.store.active()
        # One active order per symbol avoids reversing against an unfilled intent.
        if any(o.symbol==signal.symbol for o,_,_ in active):return reject('symbol_order_pending')
        price=tick.ask if signal.side=='buy' else tick.bid
        fee_multiplier=D(1)+self.config.fee_bps/D(10000)
        reserved=sum(((o.quantity-filled)*o.limit_price*fee_multiplier for o,_,filled in active if o.side=='buy'),D(0))
        throttle=max(D(0),D(1)-self.drawdown/self.drawdown_limit)
        risk_dollars=portfolio.equity*self.config.risk_fraction*throttle
        vol_quantity=risk_dollars/(price*max(signal.volatility,self.config.volatility_floor))
        budget_quantity=min(signal.budget,self.config.max_order_notional)/(price*fee_multiplier)
        if signal.side=='buy':
            cash_quantity=max(D(0),portfolio.cash-reserved)/(price*fee_multiplier)
            position_room=max(D(0),portfolio.equity*self.config.max_position_fraction-portfolio.notionals.get(signal.symbol,D(0)))
            gross_room=max(D(0),portfolio.equity*self.config.max_gross_fraction-portfolio.gross_exposure-reserved)
            quantity=min(vol_quantity,budget_quantity,cash_quantity,position_room/price,gross_room/price,tick.ask_size)
        else:
            # Reduce owned shares only. Borrowed-share execution needs another qualified adapter.
            quantity=min(budget_quantity,portfolio.positions.get(signal.symbol,D(0)),tick.bid_size)
        quantity=(quantity/self.config.quantity_step).to_integral_value(rounding=ROUND_DOWN)*self.config.quantity_step
        if quantity <= 0:return reject('no_capacity_or_owned_shares')
        from datetime import timedelta
        order=Order(id=uuid5(NAMESPACE_URL,f'{self.store.account}/{signal.id}').hex,signal_id=signal.id,
                    account=self.store.account,symbol=signal.symbol,side=signal.side,quantity=quantity,
                    limit_price=price,created_at=now,quote_at=tick.market_at,
                    expires_at=min(signal.expires_at,now+timedelta(seconds=self.config.max_order_latency)),strategy=signal.strategy)
        with self.store.db:
            self.store.db.execute('INSERT INTO orders(id,signal_id,payload,status) VALUES (?,?,?,?)',
                                 (order.id,signal.id,order.model_dump_json(),'reserved'))
            self.store.audit('risk_reserve',order.id,now)
        return RiskDecision(signal_id=signal.id,approved=True,reason='within_limits',order=order,
                            drawdown=self.drawdown,drawdown_limit=self.drawdown_limit)
