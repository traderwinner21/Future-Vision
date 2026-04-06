from datetime import datetime
from pydantic import BaseModel, Field


class TradingViewBar(BaseModel):
    secret: str
    symbol: str
    timeframe: str = "5"
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class ManualExitRequest(BaseModel):
    symbol: str
    price: float | None = None
    reason: str = "manual"


class TraderPostSignal(BaseModel):
    ticker: str
    action: str
    orderType: str = "market"
    quantity: int = 1
    timeInForce: str = "day"
    price: float | None = None
    extendedHours: bool | None = None
    note: str | None = None


class DashboardSummary(BaseModel):
    equity_today: float
    trades_today: int
    wins_today: int
    losses_today: int
    open_positions: int
    avg_score: float
    last_signal_at: datetime | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    live_ordering: bool
    database_url: str
