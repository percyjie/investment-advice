from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from ia.models import BUCKET_ORDER, AssetType, Holding

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CacheTTL(BaseModel):
    quote: int = 600
    bars: int = 21600
    calendar: int = 2592000
    news: int = 3600
    valuation: int = 86400


class LLMSettings(BaseModel):
    enabled: bool = True
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout_connect: float = 5
    timeout_read: float = 25
    max_retries: int = 2
    budget_seconds: float = 60
    temperature: float = 0.2
    max_topics: int = 5
    min_confidence: float = 0.5


class ChannelSettings(BaseModel):
    enabled: bool = True


class NotifySettings(BaseModel):
    feishu: ChannelSettings = Field(default_factory=ChannelSettings)
    dingtalk: ChannelSettings = Field(default_factory=ChannelSettings)


class Settings(BaseModel):
    timezone: str = "Asia/Shanghai"
    data_dir: str = "data"
    log_dir: str = "logs"
    portfolio_file: str = "config/portfolio.yaml"
    rules_file: str = "config/rules.yaml"
    universe_file: str = "config/universe.yaml"
    prompts_dir: str = "prompts"
    cache_ttl: CacheTTL = Field(default_factory=CacheTTL)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    notify: NotifySettings = Field(default_factory=NotifySettings)

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else PROJECT_ROOT / p


class ValuationGate(BaseModel):
    expensive: float = 80
    cheap: float = 20
    expensive_factor: float = 0.85
    cheap_factor: float = 1.10


class RegimeThresholds(BaseModel):
    risk_on_score: int = 3
    risk_off_score: int = -3
    vol_pct: float = 60
    pe_pct: float = 70
    rate_chg_bp: float = 20
    crash_chg_20d: float = -8


class RebalanceSettings(BaseModel):
    min_deviation: float = 0.03
    min_amount: float = 3000
    max_turnover: float = 0.10
    cash_floor: float = 0.20


class GuardSettings(BaseModel):
    stop_loss_loss: float = -0.15
    stop_loss_ma: int = 120
    no_chase_20d: float = 0.15
    no_chase_ma: int = 20
    no_chase_ma20: float = 0.10
    fund_fee_lock_days: int = 7
    valuation_hard_gate: float = 85
    stop_loss_exempt_buckets: list[str] = Field(default_factory=lambda: ["gold", "bond"])


class DeviationBand(BaseModel):
    warn: float
    critical: float


class RiskScanSettings(BaseModel):
    equity_deviation: DeviationBand
    cash_ratio: DeviationBand
    single_stock: float = 0.05
    loss_warn: float = -0.15
    overheat_20d: float = 0.15
    index_day_drop: DeviationBand
    pe_pct: float = 85
    rate_chg_5d_bp: float = 20
    board_exposure: float = 0.05


class Rules(BaseModel):
    risk_profile: str = "conservative"
    targets: dict[str, float]
    regime_shift: dict[str, dict[str, float]]
    bounds: dict[str, tuple[float, float]]
    valuation_gate: ValuationGate = Field(default_factory=ValuationGate)
    regime_thresholds: RegimeThresholds = Field(default_factory=RegimeThresholds)
    rebalance: RebalanceSettings = Field(default_factory=RebalanceSettings)
    caps: dict[str, float]
    guards: GuardSettings = Field(default_factory=GuardSettings)
    rounding: dict[str, int]
    risk_scan: RiskScanSettings

    @model_validator(mode="after")
    def _validate_buckets(self) -> "Rules":
        expected = set(BUCKET_ORDER)
        for field_name in ("targets", "bounds"):
            missing = expected - set(getattr(self, field_name))
            if missing:
                raise ValueError(f"{field_name} 缺少资产桶: {sorted(missing)}")
        total = sum(self.targets.values())
        if abs(total - 100) > 1e-6:
            raise ValueError(f"targets 合计必须为 100，当前为 {total}")
        for regime in ("risk_on", "risk_off"):
            shift = self.regime_shift.get(regime, {})
            missing = expected - set(shift)
            if missing:
                raise ValueError(f"regime_shift.{regime} 缺少资产桶: {sorted(missing)}")
        cap_types = {"stock", "etf", "fund"}
        if not cap_types <= set(self.caps):
            raise ValueError(f"caps 必须包含 {sorted(cap_types)}")
        if not cap_types <= set(self.rounding):
            raise ValueError(f"rounding 必须包含 {sorted(cap_types)}")
        for bucket, (low, high) in self.bounds.items():
            if low > high:
                raise ValueError(f"bounds.{bucket} 下限大于上限")
        return self


@dataclass(frozen=True)
class UniverseItem:
    code: str
    name: str
    type: AssetType
    default: bool = False


@dataclass(frozen=True)
class PortfolioConfig:
    cash: float
    holdings: tuple[Holding, ...]
    is_example: bool


@dataclass(frozen=True)
class Secrets:
    deepseek_api_key: str = ""
    feishu_webhook: str = ""
    feishu_secret: str = ""
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误（应为映射）: {path}")
    return data


def load_settings(path: Path | None = None) -> Settings:
    target = path or PROJECT_ROOT / "config" / "settings.yaml"
    return Settings(**_read_yaml(target))


def load_rules(path: Path) -> Rules:
    return Rules(**_read_yaml(path))


def load_universe(path: Path) -> dict[str, tuple[UniverseItem, ...]]:
    raw = _read_yaml(path)
    buckets: dict[str, tuple[UniverseItem, ...]] = {}
    for bucket, items in (raw.get("buckets") or {}).items():
        if bucket not in BUCKET_ORDER:
            raise ValueError(f"universe 未知资产桶: {bucket}")
        parsed: list[UniverseItem] = []
        for item in items or []:
            parsed.append(
                UniverseItem(
                    code=str(item["code"]),
                    name=str(item["name"]),
                    type=AssetType(item["type"]),
                    default=bool(item.get("default", False)),
                )
            )
        buckets[bucket] = tuple(parsed)
    return buckets


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_portfolio(path: Path) -> PortfolioConfig:
    raw = _read_yaml(path)
    cash = float(raw.get("cash", 0))
    if cash < 0:
        raise ValueError("cash 不能为负")
    holdings: list[Holding] = []
    seen: set[str] = set()
    for item in raw.get("holdings") or []:
        code = str(item["code"]).zfill(6) if str(item["code"]).isdigit() else str(item["code"])
        if code in seen:
            raise ValueError(f"portfolio 中重复标的: {code}")
        seen.add(code)
        bucket = str(item["bucket"])
        if bucket not in BUCKET_ORDER[:-1]:
            raise ValueError(f"{code} 的 bucket 非法: {bucket}")
        qty = float(item.get("qty", 0))
        cost = float(item.get("cost", 0))
        if qty <= 0:
            raise ValueError(f"{code} 的 qty 必须大于 0")
        if cost < 0:
            raise ValueError(f"{code} 的 cost 不能为负")
        holdings.append(
            Holding(
                code=code,
                name=str(item.get("name") or code),
                type=AssetType(item["type"]),
                bucket=bucket,
                qty=qty,
                cost=cost,
                bought_at=_parse_date(item.get("bought_at")),
            )
        )
    is_example = bool((raw.get("meta") or {}).get("is_example", False))
    return PortfolioConfig(cash=cash, holdings=tuple(holdings), is_example=is_example)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def load_secrets() -> Secrets:
    env_file = os.environ.get("IA_ENV_FILE") or str(Path.home() / ".config" / "ia" / "env")
    file_values = _parse_env_file(Path(env_file))

    def get(name: str) -> str:
        return os.environ.get(name) or file_values.get(name, "")

    return Secrets(
        deepseek_api_key=get("DEEPSEEK_API_KEY"),
        feishu_webhook=get("FEISHU_WEBHOOK"),
        feishu_secret=get("FEISHU_SECRET"),
        dingtalk_webhook=get("DINGTALK_WEBHOOK"),
        dingtalk_secret=get("DINGTALK_SECRET"),
    )


def load_all() -> tuple[Settings, Rules, PortfolioConfig, dict[str, tuple[UniverseItem, ...]]]:
    settings = load_settings()
    rules = load_rules(settings.path(settings.rules_file))
    portfolio = load_portfolio(settings.path(settings.portfolio_file))
    universe = load_universe(settings.path(settings.universe_file))
    return settings, rules, portfolio, universe
