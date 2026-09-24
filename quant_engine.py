"""Async paper-only tick -> alpha -> risk -> execution kernel.

Independent watchdog; no IBKR imports, production credentials or live adapter.
All components run on one event loop. SQLite transactions contain no awaits.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import stdev
from typing import AsyncIterable, Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

from quant_models import Fill, Order, Portfolio, Signal, Tick
from quant_risk import ACTIVE, RiskManager

D = Decimal


class ExecutionUnavailable(RuntimeError):
    """An execution/snapshot acknowledgement is unavailable or ambiguous."""


class ExecutionGateway(Protocol):
    environment: str
    account: str

    async def on_tick(self, tick: Tick) -> list[Fill]: ...
    async def snapshot(self, now: datetime) -> Portfolio: ...
    async def submit(self, order: Order) -> None: ...
    async def cancel(self, order_id: str) -> bool: ...


class Alpha(Protocol):
    async def on_tick(self, tick: Tick, now: datetime) -> Signal | None: ...


class UnconfiguredBrokerGateway:
    """Explicit external-service boundary. Cannot be passed to PaperEngine."""
    environment = 'unconfigured'
    account = ''

    async def on_tick(self, tick: Tick) -> list[Fill]:
        raise ExecutionUnavailable('No qualified broker execution adapter is configured')

    async def snapshot(self, now: datetime) -> Portfolio:
        raise ExecutionUnavailable('No verified broker portfolio provider is configured')

    async def submit(self, order: Order) -> None:
        raise ExecutionUnavailable('Real-money submission is not implemented in this kernel')

    async def cancel(self, order_id: str) -> bool:
        raise ExecutionUnavailable('Real-money cancellation is not implemented in this kernel')


class PaperGateway:
    """Persistent cash book; next-quote limit fills capped by displayed liquidity.

    Liquidity and fees are simulation assumptions, not a broker fill prediction.
    """
    environment = 'paper'

    def __init__(self, risk: RiskManager) -> None:
        self.risk, self.store = risk, risk.store
        self.account = self.store.account
        self.ticks: dict[str, Tick] = {}
        self.returns: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=60))

    async def on_tick(self, tick: Tick) -> list[Fill]:
        if self.risk.quotes.get(tick.symbol) != tick:
            raise ExecutionUnavailable('Quote has not passed feed validation')
        previous = self.ticks.get(tick.symbol)
        if previous and tick.market_at <= previous.market_at:
            raise ExecutionUnavailable('Non-increasing execution quote')
        if previous:
            self.returns[tick.symbol].append(float(tick.mid / previous.mid - 1))
        self.ticks[tick.symbol] = tick
        # Revalue before filling any resting order on a drawdown-crossing tick.
        if not self.risk.update_portfolio(await self.snapshot(tick.received_at), tick.received_at):
            return []
        fills = []
        liquidity = {'buy': tick.ask_size, 'sell': tick.bid_size}
        for order, state, filled in self.store.active():
            if state not in ('working', 'partial') or order.symbol != tick.symbol:
                continue
            if not order.created_at < tick.market_at < order.expires_at:
                continue
            price = tick.ask if order.side == 'buy' else tick.bid
            if (order.side == 'buy' and price > order.limit_price) or (order.side == 'sell' and price < order.limit_price):
                continue
            quantity = min(order.quantity - filled, liquidity[order.side])
            step = self.risk.config.quantity_step
            quantity = (quantity // step) * step
            if quantity <= 0:
                continue
            fill = Fill(id=uuid5(NAMESPACE_URL, f'{order.id}/{tick.market_at.isoformat()}/{filled}').hex,
                order_id=order.id, account=self.account, symbol=order.symbol, side=order.side,
                quantity=quantity, price=price, fee=quantity * price * self.risk.config.fee_bps / D(10000),
                market_at=tick.market_at, received_at=tick.received_at, source=f'paper:{tick.source}')
            if self.store.record_paper_fill(fill):
                liquidity[order.side] -= quantity
                fills.append(fill)
        return fills

    async def snapshot(self, now: datetime) -> Portfolio:
        positions = self.store.positions()
        notionals = {}
        weighted_volatility = D(0)
        for symbol, quantity in positions.items():
            tick = self.ticks.get(symbol)
            if not tick or not 0 <= (now - tick.market_at).total_seconds() <= self.risk.config.max_tick_age:
                raise ExecutionUnavailable(f'Fresh mark missing for {symbol}')
            notionals[symbol] = quantity * tick.bid  # Conservative liquidation mark.
            samples = self.returns[symbol]
            # Before enough observations, use the configured baseline, never zero volatility.
            vol = D(str(stdev(samples))) if len(samples) >= self.risk.config.min_volatility_samples else self.risk.config.target_volatility
            weighted_volatility += notionals[symbol] * max(vol, self.risk.config.volatility_floor)
        cash = D(self.store.get('cash'))
        gross = sum(notionals.values(), D(0))
        vol = weighted_volatility / gross if gross else self.risk.config.target_volatility
        return Portfolio(account=self.account, as_of=now, equity=cash + gross, cash=cash,
            gross_exposure=gross, positions=positions, notionals=notionals, volatility=vol,
            net_cash_flow=D(self.store.get('cash_flow')))

    async def submit(self, order: Order) -> None:
        if self.risk.halted:
            raise ExecutionUnavailable('Paper risk is halted')
        saved, status, _ = self.store.order(order.id)
        if saved != order or order.account != self.account or status not in ('reserved', 'working'):
            raise ExecutionUnavailable('Paper submission does not match a risk reservation')
        self.store.status(order.id, 'working')

    async def cancel(self, order_id: str) -> bool:
        _, status, _ = self.store.order(order_id)
        if status in ACTIVE:
            self.store.status(order_id, 'cancelled')
        return self.store.order(order_id)[1] in ('cancelled', 'filled', 'rejected')


class MomentumAlpha:
    """Causal rolling-return reference strategy; no claim of profitable alpha.

    Expects comparable observation spacing. A gap over five seconds resets warmup.
    """
    def __init__(self, budget: Decimal = D('10'), samples: int = 20,
                 threshold: Decimal = D('.001')) -> None:
        if not budget.is_finite() or budget <= 0 or not 3 <= samples <= 500 or not threshold.is_finite() or threshold <= 0:
            raise ValueError('Invalid momentum configuration')
        self.budget, self.samples, self.threshold = budget, samples, threshold
        self.history: dict[str, deque[Tick]] = defaultdict(lambda: deque(maxlen=samples + 1))

    async def on_tick(self, tick: Tick, now: datetime) -> Signal | None:
        history = self.history[tick.symbol]
        if history and (tick.market_at - history[-1].market_at).total_seconds() > 5:
            history.clear()
        history.append(tick)
        if len(history) < self.samples + 1:
            return None
        prices = [t.mid for t in history]
        returns = [float(b / a - 1) for a, b in zip(prices, prices[1:])]
        volatility = D(str(stdev(returns)))
        move = prices[-1] / prices[0] - 1
        if volatility <= 0 or abs(move) < self.threshold:
            return None
        return Signal(id=f'momentum/{tick.symbol}/{tick.market_at.isoformat()}', symbol=tick.symbol,
            side='buy' if move > 0 else 'sell', created_at=now, market_at=tick.market_at,
            expires_at=now + timedelta(seconds=5), strategy='rolling_momentum_reference',
            volatility=volatility, volatility_samples=len(returns), budget=self.budget)


class PaperEngine:
    def __init__(self, risk: RiskManager, alpha: Alpha, gateway: ExecutionGateway,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 watchdog_interval: float = .25) -> None:
        if gateway.environment != 'paper' or gateway.account != risk.store.account:
            raise ValueError('This engine accepts a matching PAPER gateway only')
        if not 0 < watchdog_interval <= risk.config.heartbeat_timeout / 2:
            raise ValueError('Watchdog interval must be within half the heartbeat deadline')
        self.risk, self.alpha, self.gateway, self.now = risk, alpha, gateway, now
        self.watchdog_interval = watchdog_interval
        self.actions = asyncio.Lock()
        self.stopping = asyncio.Event()
        self.running = False

    async def _cancel_due(self) -> None:
        # Latch risk before waiting for an in-flight gateway operation.
        ids = self.risk.watchdog(self.now())
        for order_id in ids:
            async with self.actions:
                if self.risk.store.order(order_id)[1] not in ACTIVE:
                    continue
                self.risk.store.status(order_id, 'cancel_pending')
                try:
                    acknowledged = await asyncio.wait_for(self.gateway.cancel(order_id), self.risk.config.io_timeout)
                    if not acknowledged:
                        raise ExecutionUnavailable('Cancellation unacknowledged')
                    if self.risk.store.order(order_id)[1] in ACTIVE:
                        self.risk.store.status(order_id, 'cancelled')
                except Exception as exc:
                    self.risk.trip('cancel_unconfirmed', self.now())
                    with self.risk.store.db:
                        self.risk.store.audit('cancel_unconfirmed', f'{order_id}: {type(exc).__name__}', self.now())
                    # Reservation remains until a later confirmed cancellation.

    async def _watch(self) -> None:
        while not self.stopping.is_set():
            await self._cancel_due()
            try:
                await asyncio.wait_for(self.stopping.wait(), self.watchdog_interval)
            except TimeoutError:
                pass

    async def _consume(self, ticks: AsyncIterable[Tick]) -> None:
        async for tick in ticks:
            # Recorded time can jump ahead without waking the wall-clock task.
            # Detect the old feed gap before observe refreshes its heartbeat.
            await self._cancel_due()
            if not self.risk.observe(tick, self.now()):
                with self.risk.store.db:
                    self.risk.store.audit('tick_rejected', tick.symbol, self.now())
                continue
            await self._cancel_due()
            async with self.actions:
                await asyncio.wait_for(self.gateway.on_tick(tick), self.risk.config.io_timeout)
                portfolio = await asyncio.wait_for(self.gateway.snapshot(self.now()), self.risk.config.io_timeout)
            if not self.risk.update_portfolio(portfolio, self.now()):
                await self._cancel_due()
                continue
            signal = await asyncio.wait_for(self.alpha.on_tick(tick, self.now()), self.risk.config.io_timeout)
            if signal is None:
                continue
            async with self.actions:
                # Refresh after alpha awaits. Never execute against its old portfolio snapshot.
                portfolio = await asyncio.wait_for(self.gateway.snapshot(self.now()), self.risk.config.io_timeout)
                decision = self.risk.decide(signal, tick, portfolio, self.now())
                if not decision.approved:
                    continue
                order = decision.order
                if order is None:
                    raise ExecutionUnavailable('Approved decision is missing its order')
                try:
                    await asyncio.wait_for(self.gateway.submit(order), self.risk.config.io_timeout)
                except BaseException:
                    if self.risk.store.order(order.id)[1] in ACTIVE:
                        self.risk.store.status(order.id, 'unknown')
                    self.risk.trip('submission_unconfirmed', self.now())
                    raise

    async def run(self, ticks: AsyncIterable[Tick]) -> None:
        if self.running or self.stopping.is_set():
            raise RuntimeError('A paper engine instance is single-use')
        self.running = True
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._watch())
                try:
                    await self._consume(ticks)
                finally:
                    self.risk.trip('feed_ended_or_engine_stopped', self.now())
                    self.stopping.set()
        finally:
            self.running = False
            self.stopping.set()
            # Bounded cleanup; failures keep reservations and a durable halt.
            await self._cancel_due()
