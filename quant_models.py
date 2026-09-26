"""Typed contracts for the isolated intraday paper execution kernel."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Symbol = Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9.]{0,9}$')]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, validate_default=True)


class Tick(Contract):
    symbol: Symbol
    market_at: AwareDatetime
    received_at: AwareDatetime
    bid: Positive
    ask: Positive
    bid_size: Nonnegative
    ask_size: Nonnegative
    source: Annotated[str, Field(min_length=1, max_length=100)]
    data_kind: Literal['live', 'recorded']
    halted: bool = False

    @model_validator(mode='after')
    def coherent(self) -> Tick:
        if self.bid > self.ask or self.market_at > self.received_at:
            raise ValueError('Crossed market or future market timestamp')
        return self

    @property
    def mid(self) -> Decimal:
        return (self.bid+self.ask)/2


class Signal(Contract):
    id: Annotated[str, Field(min_length=1, max_length=100)]
    symbol: Symbol
    side: Literal['buy', 'sell']
    created_at: AwareDatetime
    market_at: AwareDatetime
    expires_at: AwareDatetime
    strategy: Annotated[str, Field(min_length=1, max_length=100)]
    volatility: Positive  # Decimal return standard deviation per observation interval.
    volatility_samples: Annotated[int, Field(ge=2)]
    budget: Positive

    @model_validator(mode='after')
    def ordered_times(self) -> Signal:
        if not self.market_at <= self.created_at < self.expires_at:
            raise ValueError('Signal timestamps must be causal and expiring')
        return self


class Order(Contract):
    id: str
    signal_id: str
    account: str
    environment: Literal['paper'] = 'paper'
    symbol: Symbol
    side: Literal['buy', 'sell']
    quantity: Positive
    limit_price: Positive
    created_at: AwareDatetime
    quote_at: AwareDatetime
    expires_at: AwareDatetime
    strategy: str

    @model_validator(mode='after')
    def causal(self) -> Order:
        if not self.quote_at <= self.created_at < self.expires_at:
            raise ValueError('Order timestamps must be causal')
        return self


class Fill(Contract):
    id: str
    order_id: str
    account: str
    environment: Literal['paper'] = 'paper'
    symbol: Symbol
    side: Literal['buy', 'sell']
    quantity: Positive
    price: Positive
    fee: Nonnegative
    market_at: AwareDatetime
    received_at: AwareDatetime
    source: str


class Portfolio(Contract):
    account: str
    as_of: AwareDatetime
    equity: Positive
    cash: Nonnegative
    gross_exposure: Nonnegative
    positions: dict[Symbol, Nonnegative] = Field(default_factory=dict)
    notionals: dict[Symbol, Nonnegative] = Field(default_factory=dict)
    volatility: Positive
    net_cash_flow: Decimal = Field(default=Decimal(0), allow_inf_nan=False)

    @model_validator(mode='after')
    def balanced(self) -> Portfolio:
        # This kernel is a cash, long-only paper book. Margin books need another schema.
        if self.gross_exposure != sum(self.notionals.values(), Decimal(0)):
            raise ValueError('Gross exposure does not match position marks')
        if self.equity != self.cash + self.gross_exposure:
            raise ValueError('Paper equity does not reconcile with cash and holdings')
        if set(k for k,v in self.positions.items() if v) != set(k for k,v in self.notionals.items() if v):
            raise ValueError('Every holding requires a position mark')
        return self


class RiskDecision(Contract):
    signal_id: str
    approved: bool
    reason: str
    drawdown: Nonnegative
    drawdown_limit: Positive
    order: Order | None = None

    @model_validator(mode='after')
    def consistent(self) -> RiskDecision:
        if self.approved != (self.order is not None):
            raise ValueError('Only approved risk decisions may contain an order')
        return self


class RiskConfig(Contract):
    max_drawdown: Annotated[Decimal, Field(gt=0, le=Decimal('.25'))] = Decimal('.04')
    min_drawdown: Positive = Decimal('.01')
    target_volatility: Positive = Decimal('.01')
    volatility_floor: Positive = Decimal('.002')
    risk_fraction: Annotated[Decimal, Field(gt=0, le=Decimal('.02'))] = Decimal('.001')
    max_position_fraction: Annotated[Decimal, Field(gt=0, le=1)] = Decimal('.05')
    max_gross_fraction: Annotated[Decimal, Field(gt=0, le=1)] = Decimal('.25')
    max_order_notional: Positive = Decimal('100')
    fee_bps: Annotated[Decimal, Field(ge=0, le=100)] = Decimal('10')
    quantity_step: Positive = Decimal('.000001')
    max_tick_age: Annotated[float, Field(gt=0, le=60, allow_inf_nan=False)] = 5
    heartbeat_timeout: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 10
    max_order_latency: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 10
    io_timeout: Annotated[float, Field(gt=0, le=30, allow_inf_nan=False)] = 2
    min_volatility_samples: Annotated[int, Field(ge=3, le=500)] = 20

    @model_validator(mode='after')
    def limits(self) -> RiskConfig:
        if self.min_drawdown > self.max_drawdown:
            raise ValueError('Minimum drawdown threshold exceeds the maximum')
        if self.max_position_fraction > self.max_gross_fraction:
            raise ValueError('Per-position fraction exceeds portfolio gross limit')
        return self
