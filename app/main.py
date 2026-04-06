from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, get_db
from .models import Bar, Position, SignalLog
from .schemas import DashboardSummary, HealthResponse, ManualExitRequest, TraderPostSignal, TradingViewBar
from .strategy import (
    SUPPORTED,
    TRADERSPOST_TICKERS,
    Decision,
    decide,
    get_open_position,
    get_recent_bars,
    normalize_symbol,
    now_utc,
    pnl_dollars,
    position_size_for,
    prune_old_bars,
)
from .traderspost import send_signal

settings.data_path
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Railway AI Futures Bot", version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        live_ordering=settings.enable_live_ordering,
        database_url=settings.database_url,
    )


@app.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/dashboard")
def api_dashboard(db: Session = Depends(get_db)):
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    closed_today = list(db.scalars(select(Position).where(Position.status == "closed", Position.exit_time >= start).order_by(Position.exit_time.desc())))
    open_positions = list(db.scalars(select(Position).where(Position.status == "open").order_by(Position.entry_time.desc())))
    recent_signals = list(db.scalars(select(SignalLog).order_by(SignalLog.created_at.desc()).limit(20)))

    equity_today = round(sum((p.pnl_dollars or 0.0) for p in closed_today), 2)
    wins_today = sum(1 for p in closed_today if (p.pnl_dollars or 0) > 0)
    losses_today = sum(1 for p in closed_today if (p.pnl_dollars or 0) <= 0)
    avg_score = round(sum((s.score or 0.0) for s in recent_signals) / len(recent_signals), 4) if recent_signals else 0.0

    summary = DashboardSummary(
        equity_today=equity_today,
        trades_today=len(closed_today),
        wins_today=wins_today,
        losses_today=losses_today,
        open_positions=len(open_positions),
        avg_score=avg_score,
        last_signal_at=recent_signals[0].created_at if recent_signals else None,
    )

    chart = []
    running = 0.0
    for p in reversed(closed_today[-100:]):
        running += p.pnl_dollars or 0.0
        chart.append({"time": p.exit_time.isoformat() if p.exit_time else None, "equity": round(running, 2)})

    return {
        "summary": summary.model_dump(mode="json"),
        "open_positions": [serialize_position(p) for p in open_positions],
        "closed_positions": [serialize_position(p) for p in closed_today[:50]],
        "signals": [serialize_signal(s) for s in recent_signals],
        "equity_curve": chart,
    }


@app.get("/api/trades")
def api_trades(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Position).order_by(Position.entry_time.desc()).limit(200)))
    return [serialize_position(p) for p in rows]


@app.post("/webhook/tradingview/bar")
async def tradingview_bar(payload: TradingViewBar, db: Session = Depends(get_db)):
    if payload.secret != settings.tradingview_secret:
        raise HTTPException(status_code=401, detail="Invalid TradingView secret")

    symbol = normalize_symbol(payload.symbol)
    if symbol not in SUPPORTED:
        raise HTTPException(status_code=400, detail=f"Unsupported symbol: {payload.symbol}")

    bar = Bar(
        symbol=symbol,
        timeframe=payload.timeframe,
        timestamp=payload.timestamp.replace(tzinfo=None),
        open=payload.open,
        high=payload.high,
        low=payload.low,
        close=payload.close,
        volume=payload.volume,
    )
    db.merge(bar)
    db.commit()

    prune_old_bars(db, symbol, payload.timeframe)
    db.commit()

    bars = get_recent_bars(db, symbol, payload.timeframe, limit=250)
    if len(bars) < 40:
        return {"status": "warming_up", "symbol": symbol, "bars": len(bars)}

    from .strategy import bars_to_frame
    df = bars_to_frame(bars)
    current_position = get_open_position(db, symbol)
    decision = decide(df, symbol, current_position)

    result = await apply_decision(db, symbol, payload.close, decision)
    db.commit()
    return result


@app.post("/api/manual/exit")
async def manual_exit(req: ManualExitRequest, db: Session = Depends(get_db)):
    symbol = normalize_symbol(req.symbol)
    pos = get_open_position(db, symbol)
    if not pos:
        raise HTTPException(status_code=404, detail="No open position")

    exit_price = req.price if req.price is not None else pos.entry_price
    await close_position(db, pos, exit_price=exit_price, reason=req.reason, score=pos.model_score or 0.5)
    db.commit()
    return {"status": "closed", "position": serialize_position(pos)}


async def apply_decision(db: Session, symbol: str, close: float, decision: Decision):
    pos = get_open_position(db, symbol)
    if decision.action == "HOLD":
        log = SignalLog(
            symbol=symbol,
            signal_type="model",
            action="HOLD",
            score=decision.score,
            accepted=False,
            payload_json=json.dumps({"symbol": symbol, "close": close}),
            response_text=None,
            reason=decision.reason,
        )
        db.add(log)
        db.flush()
        return {"status": "hold", "symbol": symbol, "score": round(decision.score, 4), "reason": decision.reason}

    if decision.action == "BUY" and not pos:
        qty = position_size_for(symbol)
        payload = TraderPostSignal(
            ticker=TRADERSPOST_TICKERS[symbol],
            action="buy",
            orderType="market",
            quantity=qty,
            timeInForce=settings.default_time_in_force,
            price=close,
            note=f"AI long entry {symbol}",
        )
        await send_signal(db, symbol, "entry", "BUY", decision.score, payload, decision.reason)
        position = Position(
            symbol=symbol,
            side="long",
            qty=qty,
            status="open",
            entry_time=now_utc(),
            entry_price=close,
            stop_price=float(decision.stop_price),
            target_price=float(decision.target_price),
            model_score=decision.score,
        )
        db.add(position)
        db.flush()
        return {"status": "entered_long", "symbol": symbol, "score": round(decision.score, 4), "position": serialize_position(position)}

    if decision.action == "SELL" and not pos:
        qty = position_size_for(symbol)
        payload = TraderPostSignal(
            ticker=TRADERSPOST_TICKERS[symbol],
            action="sell",
            orderType="market",
            quantity=qty,
            timeInForce=settings.default_time_in_force,
            price=close,
            note=f"AI short entry {symbol}",
        )
        await send_signal(db, symbol, "entry", "SELL", decision.score, payload, decision.reason)
        position = Position(
            symbol=symbol,
            side="short",
            qty=qty,
            status="open",
            entry_time=now_utc(),
            entry_price=close,
            stop_price=float(decision.stop_price),
            target_price=float(decision.target_price),
            model_score=decision.score,
        )
        db.add(position)
        db.flush()
        return {"status": "entered_short", "symbol": symbol, "score": round(decision.score, 4), "position": serialize_position(position)}

    if decision.action == "EXIT_LONG" and pos and pos.side == "long":
        await close_position(db, pos, exit_price=close, reason=decision.reason, score=decision.score)
        return {"status": "exited_long", "symbol": symbol, "score": round(decision.score, 4), "position": serialize_position(pos)}

    if decision.action == "EXIT_SHORT" and pos and pos.side == "short":
        await close_position(db, pos, exit_price=close, reason=decision.reason, score=decision.score)
        return {"status": "exited_short", "symbol": symbol, "score": round(decision.score, 4), "position": serialize_position(pos)}

    return {"status": "ignored", "symbol": symbol, "reason": "position_state_conflict", "score": round(decision.score, 4)}


async def close_position(db: Session, pos: Position, exit_price: float, reason: str, score: float):
    action = "exit"
    payload = TraderPostSignal(
        ticker=TRADERSPOST_TICKERS[pos.symbol],
        action=action,
        orderType="market",
        quantity=pos.qty,
        timeInForce=settings.default_time_in_force,
        price=exit_price,
        note=f"AI exit {pos.symbol} reason={reason}",
    )
    await send_signal(db, pos.symbol, "exit", action.upper(), score, payload, reason)
    pos.status = "closed"
    pos.exit_time = now_utc()
    pos.exit_price = exit_price
    pos.close_reason = reason
    points = (exit_price - pos.entry_price) if pos.side == "long" else (pos.entry_price - exit_price)
    pos.pnl_points = round(points, 4)
    pos.pnl_dollars = pnl_dollars(pos.symbol, pos.side, pos.entry_price, exit_price, pos.qty)
    db.add(pos)
    db.flush()


def serialize_position(p: Position) -> dict:
    return {
        "id": p.id,
        "symbol": p.symbol,
        "side": p.side,
        "qty": p.qty,
        "status": p.status,
        "entry_time": p.entry_time.isoformat() if p.entry_time else None,
        "entry_price": p.entry_price,
        "stop_price": p.stop_price,
        "target_price": p.target_price,
        "exit_time": p.exit_time.isoformat() if p.exit_time else None,
        "exit_price": p.exit_price,
        "pnl_points": p.pnl_points,
        "pnl_dollars": p.pnl_dollars,
        "close_reason": p.close_reason,
        "model_score": p.model_score,
    }


def serialize_signal(s: SignalLog) -> dict:
    return {
        "id": s.id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "symbol": s.symbol,
        "signal_type": s.signal_type,
        "action": s.action,
        "score": s.score,
        "accepted": s.accepted,
        "reason": s.reason,
        "response_text": s.response_text,
        "payload_json": s.payload_json,
    }
