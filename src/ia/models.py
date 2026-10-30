from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class AssetType(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    FUND = "fund"


class Bucket(StrEnum):
    EQUITY_CORE = "equity_core"
    EQUITY_SATELLITE = "equity_satellite"
    STOCK = "stock"
    GOLD = "gold"
    BOND = "bond"
    CASH = "cash"


BUCKET_ORDER: tuple[str, ...] = (
    "equity_core",
    "equity_satellite",
    "stock",
    "gold",
    "bond",
    "cash",
)
EQUITY_BUCKETS: tuple[str, ...] = ("equity_core", "equity_satellite", "stock")
BUCKET_LABELS: dict[str, str] = {
    "equity_core": "宽基",
    "equity_satellite": "行业",
    "stock": "个股",
    "gold": "黄金",
    "bond": "债基",
    "cash": "现金",
}


class Regime(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"

    @property
    def label(self) -> str:
        return {"risk_on": "进攻", "neutral": "中性", "risk_off": "防守"}[self.value]


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

    @property
    def label(self) -> str:
        return {"BUY": "买入", "SELL": "卖出", "HOLD": "持有"}[self.value]


class ReasonCode(StrEnum):
    DEVIATION_UP = "DEVIATION_UP"
    DEVIATION_DOWN = "DEVIATION_DOWN"
    REGIME_SHIFT = "REGIME_SHIFT"
    CASH_FLOOR = "CASH_FLOOR"
    SINGLE_CAP = "SINGLE_CAP"
    STOP_LOSS = "STOP_LOSS"
    NO_CHASE = "NO_CHASE"
    VALUATION_GATE = "VALUATION_GATE"
    TURNOVER_CAP = "TURNOVER_CAP"
    FUND_FEE_LOCK = "FUND_FEE_LOCK"
    ROUNDING = "ROUNDING"
    NOISE = "NOISE"

    @property
    def label(self) -> str:
        return {
            "DEVIATION_UP": "低配加仓",
            "DEVIATION_DOWN": "超配减仓",
            "REGIME_SHIFT": "市场状态变化",
            "CASH_FLOOR": "现金底线",
            "SINGLE_CAP": "单一标的超限",
            "STOP_LOSS": "止损",
            "NO_CHASE": "短期过热",
            "VALUATION_GATE": "估值门控",
            "TURNOVER_CAP": "换手上限",
            "FUND_FEE_LOCK": "赎回费锁定",
            "ROUNDING": "整手限制",
            "NOISE": "偏离过小",
        }[self.value]


class Level(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

    @property
    def label(self) -> str:
        return {"green": "正常", "yellow": "注意", "red": "风险"}[self.value]

    @property
    def badge(self) -> str:
        return {"green": "🟢", "yellow": "🟡", "red": "🔴"}[self.value]


@dataclass(frozen=True)
class Bar:
    day: date
    close: float


@dataclass(frozen=True)
class Holding:
    code: str
    name: str
    type: AssetType
    bucket: str
    qty: float
    cost: float
    bought_at: date | None = None


@dataclass(frozen=True)
class Position:
    code: str
    name: str
    type: AssetType
    bucket: str
    qty: float
    cost: float
    price: float
    price_date: date
    market_value: float
    weight: float
    pnl_pct: float
    ma20: float | None = None
    ma120: float | None = None
    chg_20d: float | None = None
    bought_at: date | None = None
    nav_date: date | None = None
    stale: bool = False

    @property
    def cost_value(self) -> float:
        return self.qty * self.cost


@dataclass(frozen=True)
class Portfolio:
    as_of: date
    cash: float
    positions: tuple[Position, ...]
    total_assets: float
    is_example: bool = False

    @property
    def cash_ratio(self) -> float:
        if self.total_assets <= 0:
            return 0.0
        return self.cash / self.total_assets

    def bucket_weights(self) -> dict[str, float]:
        weights = {bucket: 0.0 for bucket in BUCKET_ORDER}
        for pos in self.positions:
            weights[pos.bucket] += pos.weight
        weights["cash"] = self.cash_ratio
        return weights

    def by_code(self) -> dict[str, Position]:
        return {pos.code: pos for pos in self.positions}

    def positions_in(self, bucket: str) -> tuple[Position, ...]:
        return tuple(p for p in self.positions if p.bucket == bucket)


@dataclass(frozen=True)
class BoardPerf:
    name: str
    chg_pct: float
    net_inflow_yi: float | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    as_of: date
    index_close: float
    index_chg_pct: float
    index_chg_20d: float
    ma20: float
    ma60: float
    ma200: float
    vol_20d: float
    vol_pct: float
    pe_pct: float
    rate_10y: float
    rate_chg_60d_bp: float
    rate_chg_5d_bp: float
    board_top: tuple[BoardPerf, ...] = ()
    board_bottom: tuple[BoardPerf, ...] = ()
    board_flow_top: tuple[BoardPerf, ...] = ()
    data_date: date | None = None


@dataclass(frozen=True)
class RegimeVote:
    name: str
    passed: bool
    detail: str
    score: int


@dataclass(frozen=True)
class RegimeResult:
    regime: Regime
    score: int
    votes: tuple[RegimeVote, ...]
    override: bool = False

    def vote_summary(self) -> str:
        return " ".join(f"{v.name}{'↑' if v.score > 0 else '↓' if v.score < 0 else '·'}" for v in self.votes)


@dataclass(frozen=True)
class TargetWeights:
    regime: Regime
    score: int
    weights: dict[str, float] = field(default_factory=dict)

    def get(self, bucket: str) -> float:
        return self.weights.get(bucket, 0.0)


@dataclass(frozen=True)
class Trade:
    action: Action
    code: str
    name: str
    type: AssetType
    bucket: str
    qty: float
    amount_est: float
    price: float
    cur_w: float
    tgt_w: float
    reason: ReasonCode
    note: str = ""

    @property
    def is_buy(self) -> bool:
        return self.action is Action.BUY


@dataclass(frozen=True)
class BlockedTrade:
    code: str
    name: str
    reason: ReasonCode
    note: str


@dataclass(frozen=True)
class Advice:
    as_of: date
    portfolio: Portfolio
    market: MarketSnapshot | None
    regime: RegimeResult
    target: TargetWeights
    trades: tuple[Trade, ...]
    blocked: tuple[BlockedTrade, ...]
    cash_after: float
    data_stale: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Alert:
    rule_id: str
    level: Level
    title: str
    detail: str
    action_hint: str = ""


@dataclass(frozen=True)
class ScanResult:
    as_of: date
    level: Level
    alerts: tuple[Alert, ...]
    regime: RegimeResult | None = None
    market: MarketSnapshot | None = None

    def should_act(self) -> bool:
        return any(a.level is Level.RED for a in self.alerts)


@dataclass(frozen=True)
class HotspotTopic:
    name: str
    summary: str
    direction: str
    confidence: float
    related: tuple[tuple[str, str, str], ...] = ()
    evidence: tuple[str, ...] = ()
    risk: str = ""

    @property
    def direction_label(self) -> str:
        return {"bullish": "利好", "bearish": "利空", "neutral": "中性"}.get(self.direction, "中性")


@dataclass(frozen=True)
class HotspotResult:
    available: bool
    market_summary: str = ""
    topics: tuple[HotspotTopic, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class ReportSection:
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    kind: str
    title: str
    level: Level
    headline: str
    sections: tuple[ReportSection, ...] = ()
    footer: str = ""
    as_of: date | None = None
    generated_at: datetime | None = None


@dataclass(frozen=True)
class NewsItem:
    title: str
    summary: str
    published_at: datetime

    def clip(self, n: int = 120) -> str:
        text = f"{self.title}｜{self.summary}" if self.summary else self.title
        return text if len(text) <= n else text[: n - 1] + "…"
