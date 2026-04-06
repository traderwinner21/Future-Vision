# MES / MNQ / MGC AI Trading Bot for Railway

A production-style starter for:
- TradingView -> FastAPI webhook -> AI/rules filter -> TradersPost webhook -> Tradovate
- SQL-backed trade journal and dashboard
- MES, MNQ, MGC support
- Railway deployment with persistent storage

## What this project does

1. Receives bar updates from TradingView on `/webhook/tradingview/bar`
2. Stores bar history in SQLite
3. Builds features (EMA spread, ATR, volatility, breakout, volume ratio)
4. Scores each bar with XGBoost if a trained model exists, otherwise uses a safe rule-based fallback
5. Sends approved orders to TradersPost
6. Stores every decision and every position in SQL for journal + dashboard

## Important

This is a deployable framework, not a promise of profitability. You must train and validate the model on your own MES/MNQ/MGC data before live trading.

## Railway setup

Use a mounted volume at `/data` so SQLite, bar history, and trained model files survive redeploys.

Recommended Railway variables:

- `APP_ENV=production`
- `DATABASE_URL=sqlite:////data/trading.db`
- `TRADERSPOST_WEBHOOK=...`
- `TRADINGVIEW_SECRET=choose-a-long-random-string`
- `POSITION_SIZE_MES=1`
- `POSITION_SIZE_MNQ=1`
- `POSITION_SIZE_MGC=1`
- `MODEL_THRESHOLD_LONG=0.58`
- `MODEL_THRESHOLD_SHORT=0.42`
- `MAX_BARS_PER_SYMBOL=3000`
- `RISK_ATR_MULTIPLIER=1.8`
- `TAKE_PROFIT_R_MULTIPLIER=2.2`
- `ENABLE_LIVE_ORDERING=true`

## TradingView alert body example

Point alerts for MES/MNQ/MGC to `/webhook/tradingview/bar` and send JSON like:

```json
{
  "secret": "your-secret",
  "symbol": "MNQ",
  "timeframe": "5",
  "timestamp": "2026-04-05T14:30:00Z",
  "open": 18245.25,
  "high": 18258.00,
  "low": 18236.50,
  "close": 18254.75,
  "volume": 2240
}
```

Supported symbol inputs:
- `MES`, `MNQ`, `MGC`
- `MES1!`, `MNQ1!`, `MGC1!`

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:
- Dashboard: `http://localhost:8000/`
- API: `http://localhost:8000/docs`

## Model training

Prepare CSV files in `/data/training/` with columns:

- `symbol`
- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`

Then call:

```bash
python -m app.training --csv /data/training/mes.csv --symbol MES
python -m app.training --csv /data/training/mnq.csv --symbol MNQ
python -m app.training --csv /data/training/mgc.csv --symbol MGC
```

This writes model files under `/data/models`.

## Journal logic

P/L is calculated from paired entry/exit prices recorded by the service. Since TradersPost does not provide a broker-state/order-feedback loop for your strategy logic, keep state in this service and reconcile fills manually if needed.
